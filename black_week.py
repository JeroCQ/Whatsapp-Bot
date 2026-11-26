"""The inbound Black Week replies configured for the two existing Meta senders."""

from datetime import datetime
from zoneinfo import ZoneInfo

VERSION = "black-week-2026-v1"
ORDER_BUTTON = "Quiero hacer mi pedido"
SUBSCRIBE_BUTTON = "Quiero promociones exclusivas"
UNSUBSCRIBE_BUTTON = "Prefiero perder las promociones"
ORDER_MESSAGE = f"{ORDER_BUTTON} de Black Week."
ACTIONS = {ORDER_BUTTON: "order", SUBSCRIBE_BUTTON: "granted", UNSUBSCRIBE_BUTTON: "revoked"}

COLOMBIA = ZoneInfo("America/Bogota")
START = datetime(2026, 11, 27, tzinfo=COLOMBIA)
END = datetime(2026, 12, 5, 18, tzinfo=COLOMBIA)
OFFERS = {
    ("tanaka", "1279018015292550"): (
        "Tanaka: 15% desde COP 70.000, 20% desde COP 120.000 y 25% desde COP 180.000 "
        "en productos elegibles. Pregunta qué productos quiere o retoma los que ya eligió. "
        "No aplica en combos."
    ),
    ("velvet", "1330184450175963"): (
        "Mochis by Velvet: caja tradicional de 6 por COP 24.000 y de 12 por COP 37.500. "
        "Si aún no eligió, pregunta si quiere caja de 6 o de 12. "
        "Punto físico: Carrera 10 #47-31, barrio El Troncal, Cali."
    ),
}


def message_id(value):
    return isinstance(value, str) and 0 < len(value) <= 300 and value.isascii() and value.isprintable() and not any(
        character.isspace() for character in value
    )


def template_reply(message, business, sender_id):
    """Only Meta quick replies with a reference to the original outbound message."""
    if message.get("type") != "button" or (business, str(sender_id)) not in OFFERS:
        return None
    context, button = message.get("context"), message.get("button")
    if not isinstance(context, dict) or not isinstance(button, dict):
        return None
    original, incoming, label = context.get("id"), message.get("id"), button.get("text")
    if (context.get("forwarded") or context.get("frequently_forwarded") or
            not message_id(original) or not message_id(incoming) or original == incoming):
        return None
    if not isinstance(label, str) or label not in ACTIONS:
        return None
    return ACTIONS[label], original, label


def order_context(business, sender_id, text, history, now):
    offer = OFFERS.get((business, str(sender_id)))
    if not offer:
        return ""
    mentioned = "black week" in (text or "").casefold()
    recent = any(row.get("role") == "user" and row.get("content") == ORDER_MESSAGE for row in history[-12:])
    if not START <= now < END:
        if not mentioned and not recent:
            return ""
        status = "aún no comenzó" if now < START else "ya terminó"
        return f"La Black Week de este negocio {status}. Ofrece continuar el pedido habitual sin aplicar esta promoción."
    if not mentioned and not recent:
        return ""
    return (
        "Información comercial confirmada para la Black Week de este negocio, en esta conversación: "
        f"{offer} Hasta el 5 de diciembre a las 6:00 p. m. de Colombia. "
        "El domicilio se cotiza por separado y la promoción no es acumulable con otras promociones. "
        "Respeta el catálogo, la disponibilidad y los controles normales del pedido. "
        "Pedir productos por esta promoción no autoriza mensajes promocionales futuros."
    )
