import os

from redis import Redis
from rq import Worker

from queue_client import QUEUE_NAME, REDIS_URL


def main():
    if not REDIS_URL:
        raise RuntimeError("REDIS_URL must be set to run queue workers")

    connection = Redis.from_url(REDIS_URL)
    worker = Worker([QUEUE_NAME], connection=connection)
    print(f"[WORKER] Starting RQ worker for queue={QUEUE_NAME}")
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
