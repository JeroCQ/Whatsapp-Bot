import argparse
import json
import logging
import signal
import threading
from concurrent.futures import ThreadPoolExecutor

from crm_service.db import database
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
    try:
        if args.once:
            result = worker.run(args.business)
            print(json.dumps(result, sort_keys=True))
            if "failed" in result.values():
                raise SystemExit(1)
            return
        while not stop.is_set():
            try:
                queued_business = consume_one(worker, sessions, args.business)
                pending = [
                    business_id
                    for business_id in settings.businesses
                    if business_id != queued_business and (not args.business or args.business == business_id)
                ]
                # Each business keeps its own budget/lock/reader; a slow brand
                # no longer delays all other brands before their first read.
                with ThreadPoolExecutor(max_workers=max(1, min(4, len(pending)))) as pool:
                    list(pool.map(worker.run, pending))
                dispatch(settings, sessions)
            except Exception as error:
                logging.error("worker_iteration_failed code=%s", type(error).__name__)
            stop.wait(settings.sync_interval)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
