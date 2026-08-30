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
