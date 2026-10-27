"""Classification helpers for actionable Gemini API failures."""

from collections.abc import Mapping

import httpx


def _error_payload(exc: Exception) -> Mapping:
    # google-genai 1.28 exposes APIError.details/code. Keep the legacy names
    # for existing wrappers without relying on string matching an exception.
    for attribute in ("response_json", "details"):
        payload = getattr(exc, attribute, None)
        if isinstance(payload, Mapping):
            error = payload.get("error", payload)
            if isinstance(error, Mapping):
                return error
    return {}


def is_depleted_prepaid_credits(exc: Exception) -> bool:
    """Return whether Gemini rejected the request because billing credit ran out."""
    error = _error_payload(exc)
    status_code = gemini_error_status_code(exc)
    status = str(error.get("status") or getattr(exc, "status", "")).upper()
    message = str(error.get("message") or getattr(exc, "message", None) or exc).lower()

    return (
        status_code == 429
        and status == "RESOURCE_EXHAUSTED"
        and ("prepayment credits are depleted" in message or "prepaid credits" in message)
    )


def gemini_error_status_code(exc: Exception) -> int | None:
    """Extract a provider HTTP status without depending on an SDK exception class."""
    error = _error_payload(exc)
    response = getattr(exc, "response", None)
    for raw_status in (
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
        error.get("code"),
        getattr(response, "status_code", None),
    ):
        if isinstance(raw_status, bool):
            continue
        try:
            return int(raw_status)
        except (TypeError, ValueError):
            continue
    return None


def is_transient_gemini_error(exc: Exception) -> bool:
    """Identify failures that are safe to retry before handing off to a human."""
    if is_depleted_prepaid_credits(exc):
        return False
    if gemini_error_status_code(exc) in {429, 500, 502, 503, 504}:
        return True
    return isinstance(exc, (ConnectionError, TimeoutError, httpx.TimeoutException, httpx.NetworkError))
