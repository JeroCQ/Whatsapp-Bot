import logging
import signal
import threading

from crm_service.db import database
from crm_service.notifications import dispatch
from crm_service.settings import Settings
from crm_service.sync import Synchronizer


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = Settings.from_env()
    engine, sessions = database(settings)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    worker = Synchronizer(settings, sessions)
    try:
        while not stop.is_set():
            try:
                worker.run()
                dispatch(settings, sessions)
            except Exception as error:
                logging.error("worker_iteration_failed code=%s", type(error).__name__)
            stop.wait(settings.sync_interval)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
