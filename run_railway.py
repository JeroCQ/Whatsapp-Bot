import os
import signal
import subprocess
import sys

import uvicorn


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def should_run_embedded_worker(environ=None) -> bool:
    """Only run an RQ worker in the web service when explicitly requested.

    A dedicated Railway worker and an embedded worker consume the same Redis
    queue nondeterministically.  Keeping this opt-in prevents an out-of-date
    web deployment from processing jobs intended for the dedicated worker.
    """
    environ = os.environ if environ is None else environ
    return bool(environ.get("REDIS_URL")) and _truthy(environ.get("RUN_WORKER_IN_WEB", "false"))


def main():
    """Start the Railway web server and optionally an in-container worker."""
    worker_process = None
    redis_url = os.getenv("REDIS_URL")
    run_worker = should_run_embedded_worker()

    if run_worker:
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
