import logging
import os
import re
from datetime import timedelta
from typing import Any, Callable

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL")
QUEUE_NAME = os.getenv("QUEUE_NAME", "whatsapp-events")
JOB_TIMEOUT = int(os.getenv("QUEUE_JOB_TIMEOUT_SECONDS", "180"))
RESULT_TTL = int(os.getenv("QUEUE_RESULT_TTL_SECONDS", "3600"))
FAILURE_TTL = int(os.getenv("QUEUE_FAILURE_TTL_SECONDS", "86400"))

_queue = None
_SAFE_JOB_ID_RE = re.compile(r"[^A-Za-z0-9_-]+")


def follow_up_delay_seconds(delay_minutes: int, environ=None) -> int:
    """Resolve follow-up delay, allowing an explicit staging-only fast override."""
    environ = os.environ if environ is None else environ
    override = str(environ.get("FOLLOW_UP_TEST_DELAY_SECONDS", "")).strip()
    if override:
        return max(1, int(override))
    return max(1, int(delay_minutes)) * 60


def queue_enabled() -> bool:
    return bool(REDIS_URL)


def web_queue_mode(environ=None) -> str:
    """Describe whether the web deployment expects an embedded or external worker."""
    environ = os.environ if environ is None else environ
    if not environ.get("REDIS_URL"):
        return "background_tasks"
    run_in_web = str(environ.get("RUN_WORKER_IN_WEB", "true")).strip().lower()
    return "external_worker" if run_in_web in {"0", "false", "no", "n", "off"} else "embedded_worker"


def sanitize_job_id(raw_job_id: str) -> str:
    """Convert provider message IDs into an RQ-safe job ID."""
    if not raw_job_id:
        return None
    safe = _SAFE_JOB_ID_RE.sub("_", str(raw_job_id)).strip("_")
    return safe[:250] or None


def get_queue():
    """Return the configured RQ queue, or None when REDIS_URL is not configured."""
    global _queue
    if not REDIS_URL:
        return None
    if _queue is None:
        from redis import Redis
        from rq import Queue

        redis_conn = Redis.from_url(REDIS_URL)
        _queue = Queue(QUEUE_NAME, connection=redis_conn, default_timeout=JOB_TIMEOUT)
    return _queue


def enqueue(func: Callable[..., Any], *args: Any, job_id: str = None, **kwargs: Any):
    """Enqueue a function for worker execution."""
    queue = get_queue()
    if queue is None:
        raise RuntimeError("REDIS_URL is not configured; queue is unavailable")

    return queue.enqueue(
        func,
        *args,
        job_id=sanitize_job_id(job_id),
        result_ttl=RESULT_TTL,
        failure_ttl=FAILURE_TTL,
        **kwargs,
    )


def enqueue_in(seconds: int, func: Callable[..., Any], *args: Any, job_id: str = None, **kwargs: Any):
    """Schedule a durable delayed job; requires Redis and an RQ scheduler worker."""
    queue = get_queue()
    if queue is None:
        raise RuntimeError("REDIS_URL is not configured; delayed jobs are unavailable")
    return queue.enqueue_in(
        timedelta(seconds=max(1, int(seconds))), func, *args,
        job_id=sanitize_job_id(job_id), result_ttl=RESULT_TTL, failure_ttl=FAILURE_TTL, **kwargs,
    )


def get_queue_stats() -> dict:
    """Return non-secret queue diagnostics for health checks and Railway logs."""
    if not queue_enabled():
        return {"enabled": False}

    try:
        from rq import Worker

        queue = get_queue()
        connection = queue.connection
        workers = Worker.all(connection=connection)
        return {
            "enabled": True,
            "queue": QUEUE_NAME,
            "web_queue_mode": web_queue_mode(),
            "queued_jobs": queue.count,
            "started_jobs": queue.started_job_registry.count,
            "failed_jobs": queue.failed_job_registry.count,
            "deferred_jobs": queue.deferred_job_registry.count,
            "workers_seen": len(workers),
        }
    except Exception as exc:
        return {"enabled": True, "queue": QUEUE_NAME, "web_queue_mode": web_queue_mode(), "error": str(exc)}
