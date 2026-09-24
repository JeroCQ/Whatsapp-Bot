"""Classification helpers for actionable Gemini API failures."""

from collections.abc import Mapping


def _error_payload(exc: Exception) -> Mapping:
    payload = getattr(exc, "response_json", None)
    if not isinstance(payload, Mapping):
        return {}
    error = payload.get("error", payload)
    return error if isinstance(error, Mapping) else {}


def is_depleted_prepaid_credits(exc: Exception) -> bool:
    """Return whether Gemini rejected the request because billing credit ran out."""
    error = _error_payload(exc)
    status_code = getattr(exc, "status_code", None) or error.get("code")
    status = str(error.get("status", "")).upper()
    message = str(error.get("message", exc)).lower()

    return (
        status_code == 429
        and status == "RESOURCE_EXHAUSTED"
        and ("prepayment credits are depleted" in message or "prepaid credits" in message)
    )


def gemini_error_status_code(exc: Exception) -> int | None:
    """Extract a provider HTTP status without depending on an SDK exception class."""
    error = _error_payload(exc)
    raw_status = getattr(exc, "status_code", None) or error.get("code")
    try:
        return int(raw_status)
    except (TypeError, ValueError):
        return None


def is_transient_gemini_error(exc: Exception) -> bool:
    """Identify failures that are safe to retry before handing off to a human."""
    if is_depleted_prepaid_credits(exc):
        return False
    if gemini_error_status_code(exc) in {429, 500, 502, 503, 504}:
        return True
    return isinstance(exc, (ConnectionError, TimeoutError))
