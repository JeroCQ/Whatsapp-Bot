import hashlib
import logging
import os
import re
import uuid
from follow_up_store import execute as follow_up_operation
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL")
QUEUE_NAME = os.getenv("QUEUE_NAME", "whatsapp-events")
JOB_TIMEOUT = int(os.getenv("QUEUE_JOB_TIMEOUT_SECONDS", "180"))
RESULT_TTL = int(os.getenv("QUEUE_RESULT_TTL_SECONDS", "3600"))
FAILURE_TTL = int(os.getenv("QUEUE_FAILURE_TTL_SECONDS", "86400"))

_queue = None
_SAFE_JOB_ID_RE = re.compile(r"[^A-Za-z0-9_-]+")
_FOLLOW_UP_TOKEN_PREFIX = "whatsapp-follow-up-token:"
_CLAIM_TOKEN_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""
_CLAIM_ATTEMPT_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
if redis.call('SET', KEYS[2], '1', 'NX', 'EX', ARGV[2]) then return 1 end
return 0
"""


def follow_up_delay_seconds(delay_minutes: int, environ=None, now=None) -> int:
    """Resolve a follow-up delay inside the daily 08:00-18:00 Colombia window."""
    environ = os.environ if environ is None else environ
    override = str(environ.get("FOLLOW_UP_TEST_DELAY_SECONDS", "")).strip()
    if override:
        return max(1, int(override))
    colombia_tz = ZoneInfo("America/Bogota")
    current = now or datetime.now(colombia_tz)
    current = current.replace(tzinfo=colombia_tz) if current.tzinfo is None else current.astimezone(colombia_tz)
    target = current + timedelta(minutes=max(1, int(delay_minutes)))

    if target.time() < time(8, 0):
        target = target.replace(hour=8, minute=0, second=0, microsecond=0)
    elif target.time() >= time(18, 0):
        target = (target + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    return max(1, int((target - current).total_seconds()))


def next_colombia_opening_delay_seconds(now=None) -> int:
    """Return seconds until the next 08:00 opening after the current turn."""
    colombia_tz = ZoneInfo("America/Bogota")
    current = now or datetime.now(colombia_tz)
    current = current.replace(tzinfo=colombia_tz) if current.tzinfo is None else current.astimezone(colombia_tz)
    if current.time() < time(8, 0):
        target = current.replace(hour=8, minute=0, second=0, microsecond=0)
    else:
        target = (current + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    return max(1, int((target - current).total_seconds()))


def meta_window_is_open(expires_at: str, now=None) -> bool:
    """Return whether a free-form message is still inside Meta's 24-hour window."""
    if not expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc) < expiry.astimezone(timezone.utc)


def colombia_service_window_is_open(now=None) -> bool:
    """Protect sends at execution time, including jobs delayed by worker outages."""
    colombia_tz = ZoneInfo("America/Bogota")
    current = now or datetime.now(colombia_tz)
    current = current.replace(tzinfo=colombia_tz) if current.tzinfo is None else current.astimezone(colombia_tz)
    return time(8, 0) <= current.time() < time(18, 0)


def _follow_up_key(phone_number: str) -> str:
    return f"{_FOLLOW_UP_TOKEN_PREFIX}{phone_number}"


def register_follow_up(phone_number: str, ttl_seconds: int, *, reference_at=None, event_id="") -> str:
    """Store the current reminder token in Redis, replacing an older reminder."""
    queue = get_queue()
    if queue is None:
        raise RuntimeError("REDIS_URL is not configured; follow-up state is unavailable")
    token = uuid.uuid4().hex
    # Keep enough time for delayed jobs and temporary worker outages, without
    # accumulating permanent per-customer state.
    result = follow_up_operation(queue.connection, "register", phone_number, token,
                                now=reference_at, ttl_seconds=max(8 * 86400, int(ttl_seconds) + 86400),
                                provider_message_id=event_id)
    if result["status"] != "registered":
        raise ValueError("stale_customer_activity")
    return token


def invalidate_follow_up(phone_number: str):
    """Cancel the current reminder without requiring a Supabase schema change."""
    try:
        queue = get_queue()
        if queue is not None:
            follow_up_operation(queue.connection, "invalidate", phone_number)
    except Exception as exc:
        logger.warning("Could not invalidate follow-up for %s: %s", phone_number, exc)


def claim_follow_up(phone_number: str, token: str) -> bool:
    """Atomically consume the reminder only when its Redis token is still current."""
    try:
        queue = get_queue()
        if queue is None:
            return False
        return follow_up_operation(queue.connection, "consume", phone_number, token)["status"] == "claimed"
    except Exception as exc:
        logger.warning("Could not claim follow-up for %s: %s", phone_number, exc)
        return False


def claim_follow_up_attempt(phone_number: str, token: str, stage: str, attempt: int) -> bool:
    """Claim one attempt without consuming the active three-message sequence."""
    try:
        queue = get_queue()
        if queue is None:
            return False
        return follow_up_operation(queue.connection, "claim", phone_number, token, stage, attempt)["status"] == "claimed"
    except Exception as exc:
        logger.warning("Could not claim follow-up attempt for %s: %s", phone_number, exc)
        return False


def record_customer_activity(phone_number, received_at, event_id=""):
    """Invalidate immediately at ingress, before a worker waits in the queue."""
    return _follow_up_operation("activity", phone_number, event_id, now=received_at)


def _follow_up_operation(operation, phone_number, token="", stage="", attempt=0, **kwargs):
    try:
        queue = get_queue()
        if queue is None:
            return {"status": "unavailable"}
        return follow_up_operation(queue.connection, operation, phone_number, token, stage, attempt, **kwargs)
    except Exception as exc:
        logger.warning("Follow-up state unavailable: %s", type(exc).__name__)
        return {"status": "unavailable"}


def reserve_follow_up_attempt(phone_number, token, stage, attempt, **kwargs):
    return _follow_up_operation("reserve", phone_number, token, stage, attempt, **kwargs)


def begin_follow_up_send(phone_number, token, stage, attempt, reservation_id, **kwargs):
    return _follow_up_operation("begin", phone_number, token, stage, attempt, reservation_id=reservation_id, **kwargs)


def finish_follow_up_attempt(phone_number, token, stage, attempt, reservation_id, **kwargs):
    return _follow_up_operation("finish", phone_number, token, stage, attempt, reservation_id=reservation_id, **kwargs)


def claim_internal_alert(identity, ttl_seconds=86400):
    try:
        from follow_up_store import keys
        queue = get_queue()
        if queue is None:
            return False
        return bool(queue.connection.set(keys(identity)[0] + ":internal-alert", "1", nx=True, ex=ttl_seconds))
    except Exception:
        return False


def queue_enabled() -> bool:
    return bool(REDIS_URL)


def follow_ups_enabled(environ=None) -> bool:
    environ = os.environ if environ is None else environ
    return str(environ.get("FOLLOW_UPS_ENABLED", "true")).strip().lower() not in {"0", "false", "no", "off"}


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
    raw = str(raw_job_id)
    safe = _SAFE_JOB_ID_RE.sub("_", raw).strip("_")
    if len(safe) > 250:
        # Long sender/message identities must not erase the unique job suffix.
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        return f"{safe[:217]}_{digest}"
    return safe or None


def get_queue():
    """Return the configured RQ queue, or None when REDIS_URL is not configured."""
    global _queue
    if not REDIS_URL:
        return None
    if _queue is None:
        from redis import Redis
        from rq import Queue

        # Webhook producers must never hang behind an unreachable Railway Redis.
        # The dedicated worker creates its own long-lived connection; this client
        # is for short queue/registry commands and therefore uses strict bounds.
        redis_conn = Redis.from_url(
            REDIS_URL,
            socket_connect_timeout=1.0,
            socket_timeout=2.0,
            retry_on_timeout=False,
            health_check_interval=30,
        )
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
        workers = [worker for worker in Worker.all(connection=connection) if QUEUE_NAME in worker.queue_names()]
        worker_commits = {}
        for worker in workers:
            value = connection.hget(worker.key, "deployment_sha")
            worker_commits[worker.name] = value.decode() if isinstance(value, bytes) else (value or "unknown")
        return {
            "enabled": True,
            "queue": QUEUE_NAME,
            "web_queue_mode": web_queue_mode(),
            "queued_jobs": queue.count,
            "started_jobs": queue.started_job_registry.count,
            "failed_jobs": queue.failed_job_registry.count,
            "deferred_jobs": queue.deferred_job_registry.count,
            "workers_seen": len(workers),
            "worker_commits": worker_commits,
        }
    except Exception as exc:
        return {"enabled": True, "queue": QUEUE_NAME, "web_queue_mode": web_queue_mode(), "error": str(exc)}
