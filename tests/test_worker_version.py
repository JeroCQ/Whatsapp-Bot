import fakeredis
from rq import Queue

from workers.runner import DeploymentWorker


def test_worker_birth_registers_sha_without_corrupting_rq_lifecycle(monkeypatch):
    redis = fakeredis.FakeRedis()
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "a" * 40)
    worker = DeploymentWorker([Queue("brand-queue", connection=redis)], connection=redis)
    worker.register_birth()
    assert redis.hget(worker.key, "deployment_sha") == b"a" * 40
    assert redis.hget(worker.key, "queues") == b"brand-queue"
    worker.register_death()
    assert redis.hexists(worker.key, "death")
