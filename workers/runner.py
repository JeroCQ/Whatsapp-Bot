import os

from redis import Redis
from rq import Worker

from queue_client import QUEUE_NAME, REDIS_URL


class DeploymentWorker(Worker):
    def register_birth(self):
        # RQ deletes/recreates its hash during birth. Writing before this hook
        # would both lose the SHA and make RQ reject its own worker as a duplicate.
        super().register_birth()
        commit_sha = os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GIT_COMMIT_SHA") or "unknown"
        self.connection.hset(self.key, "deployment_sha", commit_sha)


def main():
    if not REDIS_URL:
        raise RuntimeError("REDIS_URL must be set to run queue workers")

    connection = Redis.from_url(REDIS_URL)
    worker = DeploymentWorker([QUEUE_NAME], connection=connection)
    commit_sha = os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GIT_COMMIT_SHA") or "unknown"
    print(f"[WORKER] Starting RQ worker for queue={QUEUE_NAME} commit={commit_sha}", flush=True)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
