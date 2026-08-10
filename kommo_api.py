"""Entrada y salida para la integracion del Salesbot de Kommo."""

import logging
from typing import Any
from urllib.parse import quote

import httpx

from config import config

logger = logging.getLogger(__name__)


class InvalidKommoPayload(ValueError):
    """El webhook no contiene los datos minimos de un mensaje entrante."""


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


def extract_kommo_message(data: Any) -> tuple[str, str, str]:
    """Extrae identificadores y texto de formatos directos o anidados de Salesbot."""
    if not isinstance(data, dict):
        raise InvalidKommoPayload("El cuerpo debe ser un objeto JSON")

    chat_id = _first(data, (
        ("chat_id",), ("chat", "id"), ("message", "chat_id"),
        ("payload", "chat_id"), ("payload", "chat", "id"),
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
    # Some Salesbot HTTP widgets send `message` itself as the text value.
    if text is None and isinstance(data.get("message"), str):
        text = data["message"]

    missing = [
        name
        for name, value in (("chat_id", chat_id), ("contact_id", contact_id), ("text", text))
        if value is None
    ]
    if missing:
        raise InvalidKommoPayload(f"Faltan campos requeridos: {', '.join(missing)}")
    if not isinstance(text, str) or not text.strip():
        raise InvalidKommoPayload("El texto del mensaje debe ser una cadena no vacia")
    return str(chat_id), str(contact_id), text.strip()


async def send_message_kommo(chat_id: str, text: str) -> bool:
    """Envia texto a un chat de Kommo autenticando la Integracion Privada."""
    if not config.KOMMO_BASE_URL or not config.KOMMO_PRIVATE_TOKEN:
        logger.error("KOMMO_BASE_URL o KOMMO_PRIVATE_TOKEN no estan configurados")
        return False
    if not chat_id or not isinstance(text, str) or not text.strip():
        logger.error("No se puede enviar a Kommo un chat_id o texto vacio")
        return False

    url = f"{config.KOMMO_BASE_URL.rstrip('/')}/api/v4/chats/{quote(str(chat_id), safe='')}/messages"
    headers = {
        "Authorization": f"Bearer {config.KOMMO_PRIVATE_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {"message": {"text": text.strip()}}
    try:
        async with httpx.AsyncClient(timeout=config.KOMMO_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        return True
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Kommo rechazo el mensaje para chat_id=%s: HTTP %s - %s",
            chat_id,
            exc.response.status_code,
            exc.response.text,
        )
    except httpx.RequestError as exc:
        logger.error("Error de red enviando mensaje a Kommo chat_id=%s: %s", chat_id, exc)
    except Exception:
        logger.exception("Error inesperado enviando mensaje a Kommo chat_id=%s", chat_id)
    return False
