"""WhatsApp promotion choices. Only called behind the verified CRM webhook."""

import re
import unicodedata

QUESTION = (
    "¿Quieres recibir promociones de este negocio por WhatsApp? "
    "Puedes darte de baja cuando quieras escribiendo BAJA PROMOCIONES."
)
VERSION = "promotions-v1"
CHOICES = {"promotion:v1:yes": "granted", "promotion:v1:no": "denied"}


def normalize(value):
    if not isinstance(value, str) or len(value) > 200:
        return ""
    unaccented = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    return " ".join(re.sub(r"[.,!?]", " ", unaccented).split())


def choice_from_message(message):
    """An unrelated quick reply or an unquoted yes/no is ordinary conversation."""
    kind = message.get("type")
    context = message.get("context") or {}
    question = context.get("id") if isinstance(context, dict) else None
    if not isinstance(question, str) or not question or len(question) > 300:
        question = None
    if kind == "text":
        answer = normalize((message.get("text") or {}).get("body"))
        if answer in {"baja promociones", "no quiero recibir promociones", "no me envien promociones",
                      "no quiero recibir mas promociones", "no me escriban mas"}:
            return "revoked", None
        if answer in {"quiero recibir promociones por whatsapp", "alta promociones"}:
            return "granted", None
        if question and answer in {"si", "si quiero", "no", "no gracias"}:
            return ("granted" if answer.startswith("si") else "denied"), question
    elif kind == "interactive" and question:
        data = message.get("interactive") or {}
        if data.get("type") == "button_reply":
            reply = (data.get("button_reply") or {}).get("id")
            if isinstance(reply, str) and reply in CHOICES:
                return CHOICES[reply], question
    return None


def buttons():
    return {"type": "button", "body": {"text": QUESTION}, "action": {"buttons": [
        {"type": "reply", "reply": {"id": "promotion:v1:yes", "title": "Sí, quiero"}},
        {"type": "reply", "reply": {"id": "promotion:v1:no", "title": "No, gracias"}},
    ]}}
