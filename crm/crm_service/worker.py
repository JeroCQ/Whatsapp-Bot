import argparse
import json
import logging
import signal
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

from crm_service.db import database
from crm_service.customer_analysis import consume_analysis
from crm_service.notifications import dispatch
from crm_service.settings import Settings
from crm_service.sync import Synchronizer
from crm_service.sync_jobs import consume_one


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bounded CRM sync: synthetic sources or authenticated bot export"
    )
    parser.add_argument("--once", action="store_true", help="Run one bounded step, not an unbounded backfill")
    parser.add_argument("--business", help="Select exactly one configured business")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = Settings.from_env(service="worker")
    if args.business and args.business not in settings.businesses:
        parser.error("Unknown business")
    engine, sessions = database(settings)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    worker = Synchronizer(settings, sessions)
    # Push providers can stall independently from source reads. One local
    # background dispatcher keeps the existing sync process responsive and
    # prevents overlapping push batches in that process.
    push_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="crm-push")
    push_run: Future[int] | None = None
    analysis_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="crm-analysis") if settings.customer_insights_businesses else None
    analysis_run: Future[None] | None = None
    next_analysis = 0.0
    sync_pool = ThreadPoolExecutor(max_workers=max(1, min(4, len(settings.businesses))))
    sync_runs: dict[str, Future[dict[str, str]]] = {}
    next_sync: dict[str, float] = {}
    queued_run: Future[str | None] | None = None
    from crm_service.runtime_service import worker_pause_state, worker_acknowledge
    pause_state, next_gate_check, acknowledged = None, 0.0, ""
    try:
        if args.once:
            result = worker.run(args.business)
            print(json.dumps(result, sort_keys=True))
            if "failed" in result.values():
                raise SystemExit(1)
            return
        while not stop.is_set():
            try:
                if time.monotonic() >= next_gate_check:
                    pause_state = worker_pause_state()
                    next_gate_check = time.monotonic() + 3
                if pause_state and pause_state.get("frozen"):
                    pending = [*sync_runs.values(), queued_run, push_run, analysis_run]
                    if all(job is None or job.done() for job in pending):
                        epoch = pause_state.get("epoch", "")
                        if epoch and epoch != acknowledged:
                            worker_acknowledge(epoch)
                            acknowledged = epoch
                    stop.wait(1)
                    continue
                queued_business = None
                if queued_run is not None and queued_run.done():
                    try:
                        queued_business = queued_run.result()
                    except Exception as error:
                        logging.error("queued_sync_failed code=%s", type(error).__name__)
                    queued_run = None
                if queued_run is None:
                    # Explicit backfill jobs use the same bounded pool. They
                    # must not block the loop scheduling unrelated businesses.
                    queued_run = sync_pool.submit(consume_one, worker, sessions, args.business)
                # Finish/schedule each brand separately. Waiting for a whole
                # sweep made a slow brand delay even the next read of fast ones.
                for business_id in settings.businesses:
                    if args.business and args.business != business_id:
                        continue
                    previous = sync_runs.get(business_id)
                    if previous is not None:
                        if not previous.done():
                            continue
                        try:
                            previous.result()
                        except Exception as error:
                            logging.error("business_sync_failed code=%s", type(error).__name__)
                        del sync_runs[business_id]
                    if business_id == queued_business:
                        next_sync[business_id] = time.monotonic() + settings.sync_interval
                    elif time.monotonic() >= next_sync.get(business_id, 0):
                        sync_runs[business_id] = sync_pool.submit(worker.run, business_id)
                        next_sync[business_id] = time.monotonic() + settings.sync_interval
                if push_run is None or push_run.done():
                    if push_run is not None:
                        try:
                            push_run.result()
                        except Exception as error:
                            logging.error("push_dispatch_failed code=%s", type(error).__name__)
                    push_run = push_pool.submit(dispatch, settings, sessions)
                if (analysis_pool is not None and time.monotonic() >= next_analysis
                        and (analysis_run is None or analysis_run.done())):
                    if analysis_run is not None:
                        try:
                            analysis_run.result()
                        except Exception as error:
                            logging.error("customer_analysis_failed code=%s", type(error).__name__)
                    analysis_run = analysis_pool.submit(consume_analysis, settings, sessions, args.business)
                    next_analysis = time.monotonic() + max(5, settings.sync_interval)
            except Exception as error:
                logging.error("worker_iteration_failed code=%s", type(error).__name__)
            stop.wait(min(1, settings.sync_interval))
    finally:
        sync_pool.shutdown(wait=True)
        push_pool.shutdown(wait=True)
        if analysis_pool is not None:
            analysis_pool.shutdown(wait=True)
        engine.dispose()


if __name__ == "__main__":
    main()
