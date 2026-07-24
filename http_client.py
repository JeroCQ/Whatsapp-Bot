import logging
import time
from typing import Any, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT: Tuple[int, int] = (3, 20)
MEDIA_TIMEOUT: Tuple[int, int] = (5, 60)

_RETRY_STATUS_CODES = (429, 500, 502, 503, 504)

_session = requests.Session()
_adapter = HTTPAdapter(
    max_retries=Retry(
        total=3,
        connect=3,
        read=2,
        backoff_factor=0.6,
        status_forcelist=_RETRY_STATUS_CODES,
        allowed_methods=frozenset(["GET", "PUT", "DELETE", "HEAD", "OPTIONS"]),
        respect_retry_after_header=True,
    )
)
_session.mount("http://", _adapter)
_session.mount("https://", _adapter)


def request(method: str, url: str, *, timeout: Tuple[int, int] = DEFAULT_TIMEOUT, **kwargs: Any) -> requests.Response:
    """Send an HTTP request with bounded timeouts and shared connection pooling."""
    started_at = time.perf_counter()
    response = _session.request(method, url, timeout=timeout, **kwargs)
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    host = requests.utils.urlparse(url).netloc
    print(f"[METRIC] http_request method={method} host={host} status={response.status_code} duration_ms={duration_ms}")
    return response


def get(url: str, *, timeout: Tuple[int, int] = DEFAULT_TIMEOUT, **kwargs: Any) -> requests.Response:
    return request("GET", url, timeout=timeout, **kwargs)


def post(url: str, *, timeout: Tuple[int, int] = DEFAULT_TIMEOUT, **kwargs: Any) -> requests.Response:
    return request("POST", url, timeout=timeout, **kwargs)


def put(url: str, *, timeout: Tuple[int, int] = DEFAULT_TIMEOUT, **kwargs: Any) -> requests.Response:
    return request("PUT", url, timeout=timeout, **kwargs)
