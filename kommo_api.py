"""Inbound and outbound helpers for the Kommo Salesbot widget protocol."""

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import jwt

from config import config

logger = logging.getLogger(__name__)

_CONTINUE_PATH = re.compile(r"^/api/v4/(?:salesbot|marketingbot)/\d+/continue/\d+/?$")
_MAX_SHOW_HANDLERS = 10
_MAX_SHOW_TEXT = 80


class InvalidKommoPayload(ValueError):
    """The webhook does not contain a usable Kommo message."""


class InvalidKommoToken(ValueError):
    """The Salesbot widget JWT could not be authenticated."""


@dataclass(frozen=True)
class KommoWidgetRequest:
    """Authenticated data supplied by Kommo's ``widget_request`` handler."""

    contact_id: str
    message_text: str
    return_url: str


def _value_at(data: dict[str, Any], *path: str) -> Any:
    value: Any = data
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _first(data: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> Any:
    for path in paths:
        value = _value_at(data, *path)
        if value is not None and value != "":
            return value
    return None


def _configured_kommo_host() -> str:
    return (urlsplit(config.KOMMO_BASE_URL or "").hostname or "").lower()


def validate_return_url(return_url: Any) -> str:
    """Allow continuation callbacks only to this configured Kommo account."""
    if not isinstance(return_url, str):
        raise InvalidKommoPayload("Falta return_url del Salesbot")
    parsed = urlsplit(return_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.lower() != _configured_kommo_host()
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or not _CONTINUE_PATH.fullmatch(parsed.path)
    ):
        raise InvalidKommoPayload("return_url no pertenece a la cuenta Kommo configurada")
    return return_url


def validate_widget_token(token: Any) -> dict[str, Any]:
    """Verify the HS256 one-time JWT sent by the Kommo widget handler."""
    if not config.KOMMO_INTEGRATION_SECRET:
        raise InvalidKommoToken("KOMMO_INTEGRATION_SECRET no esta configurado")
    if not isinstance(token, str) or not token:
        raise InvalidKommoToken("Falta el token JWT de Kommo")
    try:
        claims = jwt.decode(
            token,
            config.KOMMO_INTEGRATION_SECRET,
            algorithms=["HS256"],
            options={
                "verify_aud": False,
                "require": ["exp", "iat", "iss", "client_uuid"],
            },
        )
    except jwt.PyJWTError as exc:
        raise InvalidKommoToken("Token JWT de Kommo invalido") from exc

    expected_host = _configured_kommo_host()
    issuer_host = (urlsplit(str(claims.get("iss", ""))).hostname or "").lower()
    if not expected_host or issuer_host != expected_host:
        raise InvalidKommoToken("El token pertenece a otra cuenta Kommo")
    if config.KOMMO_INTEGRATION_ID and claims.get("client_uuid") != config.KOMMO_INTEGRATION_ID:
        raise InvalidKommoToken("El token pertenece a otra integracion")
    return claims


def extract_widget_request(data: Any) -> KommoWidgetRequest:
    """Authenticate and extract Kommo's official Salesbot widget payload."""
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        raise InvalidKommoPayload("El cuerpo widget_request debe contener data")
    validate_widget_token(data.get("token"))
    payload = data["data"]
    contact_id = payload.get("contact_id") or payload.get("lead_id")
    message = payload.get("message")
    if contact_id is None:
        raise InvalidKommoPayload("Falta contact_id en data")
    if not isinstance(message, str) or not message.strip():
        raise InvalidKommoPayload("Falta message en data")
    return KommoWidgetRequest(
        contact_id=str(contact_id),
        message_text=message.strip(),
        return_url=validate_return_url(data.get("return_url")),
    )


def is_widget_request(data: Any) -> bool:
    return isinstance(data, dict) and ("return_url" in data or "token" in data)


def extract_kommo_message(data: Any) -> tuple[str, str, str]:
    """Extract the legacy direct/talk payload retained for manual diagnostics."""
    if not isinstance(data, dict):
        raise InvalidKommoPayload("El cuerpo debe ser un objeto JSON")
    chat_id = _first(data, (
        ("talk_id",), ("chat_id",), ("talk", "id"), ("chat", "id"),
        ("message", "talk_id"), ("message", "chat_id"),
        ("payload", "talk_id"), ("payload", "talk", "id"),
        ("payload", "chat_id"), ("payload", "chat", "id"),
        ("data", "talk_id"), ("data", "talk", "id"),
        ("data", "chat_id"), ("data", "chat", "id"),
    ))
    contact_id = _first(data, (
        ("contact_id",), ("contact", "id"), ("message", "contact_id"),
        ("payload", "contact_id"), ("payload", "contact", "id"),
        ("data", "contact_id"), ("data", "contact", "id"),
    ))
    text = _first(data, (
        ("text",), ("message", "text"), ("message", "body"),
        ("payload", "text"), ("payload", "message", "text"),
        ("payload", "message", "body"), ("data", "text"),
        ("data", "message", "text"), ("data", "message", "body"),
    ))
    if text is None and isinstance(data.get("message"), str):
        text = data["message"]
    missing = [name for name, value in (("chat_id", chat_id), ("contact_id", contact_id), ("text", text)) if value is None]
    if missing:
        raise InvalidKommoPayload(f"Faltan campos requeridos: {', '.join(missing)}")
    if not isinstance(text, str) or not text.strip():
        raise InvalidKommoPayload("El texto del mensaje debe ser una cadena no vacia")
    return str(chat_id), str(contact_id), text.strip()


def _show_handlers(text: str) -> list[dict[str, Any]]:
    """Split replies into Kommo's maximum ten 80-character show handlers."""
    remaining = " ".join(text.split())
    chunks: list[str] = []
    while remaining and len(chunks) < _MAX_SHOW_HANDLERS:
        if len(remaining) <= _MAX_SHOW_TEXT:
            chunks.append(remaining)
            remaining = ""
            break
        split_at = remaining.rfind(" ", 0, _MAX_SHOW_TEXT + 1)
        if split_at < 1:
            split_at = _MAX_SHOW_TEXT
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks[-1] = chunks[-1][:_MAX_SHOW_TEXT - 1].rstrip() + "…"
    return [{"handler": "show", "params": {"type": "text", "value": chunk}} for chunk in chunks]


async def continue_salesbot(return_url: str, text: str) -> bool:
    """Publish the agent reply through the original channel and resume Salesbot."""
    try:
        safe_url = validate_return_url(return_url)
    except InvalidKommoPayload as exc:
        logger.error("No se envio la continuacion de Kommo: %s", exc)
        return False
    if not config.KOMMO_PRIVATE_TOKEN or not isinstance(text, str) or not text.strip():
        logger.error("KOMMO_PRIVATE_TOKEN o texto de respuesta no configurado")
        return False
    headers = {
        "Authorization": f"Bearer {config.KOMMO_PRIVATE_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {"data": {"status": "success"}, "execute_handlers": _show_handlers(text)}
    try:
        async with httpx.AsyncClient(timeout=config.KOMMO_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(safe_url, headers=headers, json=payload)
            response.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        logger.error("Kommo rechazo la continuacion del Salesbot: %s", exc)
        return False


async def send_message_kommo(chat_id: str, text: str) -> bool:
    """Legacy direct-talk sender retained only for backwards compatibility."""
    if not config.KOMMO_BASE_URL or not config.KOMMO_PRIVATE_TOKEN:
        logger.error("KOMMO_BASE_URL o KOMMO_PRIVATE_TOKEN no estan configurados")
        return False
    if not chat_id or not isinstance(text, str) or not text.strip():
        return False
    url = f"{config.KOMMO_BASE_URL.rstrip('/')}/api/v4/talks/{quote(str(chat_id), safe='')}/send_message"
    headers = {"Authorization": f"Bearer {config.KOMMO_PRIVATE_TOKEN}", "Content-Type": "application/json", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=config.KOMMO_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(url, headers=headers, json={"text": text.strip()})
            response.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        logger.error("Kommo rechazo el mensaje para chat_id=%s: %s", chat_id, exc)
        return False
