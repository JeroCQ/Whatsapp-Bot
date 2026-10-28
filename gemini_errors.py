"""Classification helpers for actionable Gemini API failures."""

from collections.abc import Mapping
from json import JSONDecodeError

import httpx
from pydantic import ValidationError


class GeminiResponseError(ValueError):
    """The provider returned no usable JSON object for the bot turn."""


def gemini_failure_category(exc: Exception) -> str:
    """Return an allowlisted diagnostic category without exposing response text."""
    if is_depleted_prepaid_credits(exc):
        return "prepaid_credits_depleted"
    status = gemini_error_status_code(exc)
    if status in {500, 502, 503}:
        return "provider_unavailable"
    if status == 429:
        return "rate_or_quota_limit"
    if status in {401, 403}:
        return "provider_authentication"
    if status in {408, 504}:
        return "provider_timeout"
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "network_timeout"
    if isinstance(exc, (ConnectionError, httpx.NetworkError)):
        return "network_error"
    if isinstance(exc, (GeminiResponseError, JSONDecodeError, ValidationError)):
        return "invalid_structured_response"
    return "inference_error"


def gemini_handoff_reason(category: str, status: int | None = None) -> str:
    """Build the advisor note exclusively from fixed, non-sensitive labels."""
    detail = {
        "provider_unavailable": "servicio de Google temporalmente no disponible",
        "rate_or_quota_limit": "Google limitó las solicitudes o la cuota disponible",
        "provider_authentication": "Google rechazó la autenticación o los permisos",
        "provider_timeout": "Google agotó el tiempo para responder",
        "network_timeout": "se agotó el tiempo de espera de la conexión con Google",
        "network_error": "falló la conexión con Google",
        "operation_timeout": "se agotó el presupuesto de tiempo de la operación",
        "invalid_structured_response": "respuesta de Google con formato inválido",
        "prepaid_credits_depleted": "no hay créditos prepagados",
    }.get(category, "error de inferencia")
    suffix = f" (HTTP {status})" if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599 else ""
    return f"Gemini no respondió: {detail}{suffix}"


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
