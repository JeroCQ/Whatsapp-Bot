import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL")
QUEUE_NAME = os.getenv("QUEUE_NAME", "whatsapp-events")
JOB_TIMEOUT = int(os.getenv("QUEUE_JOB_TIMEOUT_SECONDS", "180"))
RESULT_TTL = int(os.getenv("QUEUE_RESULT_TTL_SECONDS", "3600"))
FAILURE_TTL = int(os.getenv("QUEUE_FAILURE_TTL_SECONDS", "86400"))

_queue = None


def queue_enabled() -> bool:
    return bool(REDIS_URL)


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
        job_id=job_id,
        result_ttl=RESULT_TTL,
        failure_ttl=FAILURE_TTL,
        **kwargs,
    )
