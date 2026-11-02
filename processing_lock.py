import contextlib
import threading
from collections import defaultdict

from config import config

_local_locks = defaultdict(threading.Lock)


@contextlib.contextmanager
def phone_lock(phone_number: str):
    """Serialize message processing per customer phone number."""
    if config.REDIS_URL:
        from redis import Redis
        from redis.lock import Lock

        redis_conn = Redis.from_url(config.REDIS_URL)
        lock = Lock(
            redis_conn,
            name=f"phone-lock:{phone_number}",
            timeout=config.PHONE_LOCK_TTL_SECONDS,
            blocking_timeout=config.PHONE_LOCK_TTL_SECONDS,
        )
        acquired = lock.acquire(blocking=True)
        if not acquired:
            raise TimeoutError(f"No se pudo adquirir lock para {phone_number}")
        try:
            yield
        finally:
            lock.release()
    else:
        lock = _local_locks[phone_number]
        with lock:
            yield
