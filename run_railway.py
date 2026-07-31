import os
import signal
import subprocess
import sys

import uvicorn


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def dedicated_worker_count() -> int:
    """Return live RQ workers, treating Redis/probe failures as no safe consumer."""
    try:
        from queue_client import get_queue_stats

        return int(get_queue_stats().get("workers_seen", 0))
    except Exception as exc:
        print(f"[LAUNCHER WARN] Could not verify a dedicated worker: {exc}")
        return 0


def should_run_embedded_worker(environ=None, external_workers_seen=None) -> bool:
    """Run an RQ worker unless a dedicated worker was explicitly configured.

    Defaulting to an embedded worker keeps webhook jobs from getting stranded
    when a Railway service accidentally uses the web start command. Deployments
    with a verified dedicated worker must set RUN_WORKER_IN_WEB=false.
    """
    environ = os.environ if environ is None else environ
    if not environ.get("REDIS_URL"):
        return False
    if _truthy(environ.get("RUN_WORKER_IN_WEB", "true")):
        return True
    # `false` is honored only after a real consumer has been observed. Otherwise
    # every immediate and delayed job would remain stranded in Redis.
    return external_workers_seen == 0


def main():
    """Start the Railway web server and optionally an in-container worker."""
    worker_process = None
    redis_url = os.getenv("REDIS_URL")
    external_workers_seen = None
    if redis_url and not _truthy(os.getenv("RUN_WORKER_IN_WEB", "true")):
        external_workers_seen = dedicated_worker_count()
    run_worker = should_run_embedded_worker(external_workers_seen=external_workers_seen)

    if run_worker:
        if external_workers_seen == 0:
            print("[LAUNCHER WARN] RUN_WORKER_IN_WEB=false but no RQ worker was found; starting embedded fallback worker.")
        else:
            print("[LAUNCHER] REDIS_URL detected; starting embedded RQ worker subprocess.")
        worker_process = subprocess.Popen([sys.executable, "-m", "workers.runner"])
    elif redis_url:
        print("[LAUNCHER] REDIS_URL detected but RUN_WORKER_IN_WEB=false; web process will only enqueue jobs.")
    else:
        print("[LAUNCHER] REDIS_URL not set; web process will use FastAPI background tasks.")

    def _shutdown(signum, frame):
        if worker_process and worker_process.poll() is None:
            print("[LAUNCHER] Stopping embedded worker subprocess.")
            worker_process.terminate()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
    finally:
        if worker_process and worker_process.poll() is None:
            worker_process.terminate()
            try:
                worker_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker_process.kill()


if __name__ == "__main__":
    main()
