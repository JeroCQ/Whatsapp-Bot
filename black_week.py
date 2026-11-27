"""The inbound Black Week replies configured for the two existing Meta senders."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

VERSION = "black-week-2026-v1"
ORDER_BUTTON = "Quiero hacer mi pedido"
SUBSCRIBE_BUTTON = "Quiero promociones exclusivas"
UNSUBSCRIBE_BUTTON = "Prefiero perder las promociones"
ORDER_MESSAGE = f"{ORDER_BUTTON} de Black Week."
ACTIONS = {ORDER_BUTTON: "order", SUBSCRIBE_BUTTON: "granted", UNSUBSCRIBE_BUTTON: "revoked"}
TANAKA_ACTIONS = {
    **ACTIONS,
    "Seguir recibiendo promos": "granted",
    "Prefiero perder promos": "revoked",
}

COLOMBIA = ZoneInfo("America/Bogota")
START = datetime(2026, 11, 27, tzinfo=COLOMBIA)
END = datetime(2026, 12, 5, 18, tzinfo=COLOMBIA)
PREVIEW = START - timedelta(days=1)
CAMPAIGN_SOURCE = "campaign_template"
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


def message_reference(message):
    context = message.get("context")
    if not isinstance(context, dict) or context.get("forwarded") or context.get("frequently_forwarded"):
        return None
    reference = context.get("id")
    return reference if message_id(reference) and reference != message.get("id") else None


def campaign_summary(business, sender_id):
    if (business, str(sender_id)) not in OFFERS:
        return None
    prices = ("Tanaka · 15% desde COP 70.000, 20% desde COP 120.000 y 25% desde COP 180.000"
              if business == "tanaka" else "Mochis by Velvet · tradicional x6 COP 24.000 y x12 COP 37.500")
    return f"Plantilla externa · Black Week de {prices}. Vigencia del 27 de noviembre al 5 de diciembre de 2026, 6:00 p. m. (Colombia)."


def template_reply(message, business, sender_id):
    """Only Meta quick replies with a reference to the original outbound message."""
    if message.get("type") != "button" or (business, str(sender_id)) not in OFFERS:
        return None
    original, button = message_reference(message), message.get("button")
    if not original or not isinstance(button, dict) or not message_id(message.get("id")):
        return None
    label = button.get("text")
    actions = TANAKA_ACTIONS if business == "tanaka" else ACTIONS
    if not isinstance(label, str) or label not in actions:
        return None
    return actions[label], original, label


def order_context(business, sender_id, text, history, now, *, reply_to=None):
    offer = OFFERS.get((business, str(sender_id)))
    if not offer:
        return ""
    mentioned = "black week" in (text or "").casefold()
    contextual = False
    for row in history:
        metadata = row.get("metadata")
        if isinstance(metadata, dict) and metadata.get("source") == CAMPAIGN_SOURCE and metadata.get("campaign_version") == VERSION:
            contextual = contextual or (
                metadata.get("campaign_evidence") in {"template_button", "reported_send", "tagged_status"} and
                (not reply_to or metadata.get("provider_message_id") == reply_to)
            )
    recent = any(row.get("role") == "user" and row.get("content") == ORDER_MESSAGE for row in history[-12:])
    if now >= END:
        return ("La Black Week de 2026 ya terminó. Sus descuentos y precios anteriores, incluidos los del historial "
                "y del resumen guardado, son históricos. Para nuevas cotizaciones usa precios vigentes. "
                "Conserva los pedidos ya confirmados.")
    if now < PREVIEW:
        return ""
    if now < START:
        validity = ("La oferta empieza el 27 de noviembre a las 00:00 (Colombia). Presenta los valores como próximos "
                    "y no confirmes descuentos antes de esa fecha.")
    else:
        validity = "Vigente hasta el 5 de diciembre a las 6:00 p. m. de Colombia."
    attribution = ("La conversación tiene evidencia de Black Week o la menciona expresamente. " if mentioned or contextual or recent else
                   "Usa el mensaje y el historial para deducir si consulta por esta oferta. No asumas que recibió la plantilla. "
                   "Si la distinción cambia la respuesta y siguen siendo posibles dos interpretaciones, acláralo una vez. ")
    return (
        f"Información comercial confirmada para la Black Week de este negocio. {attribution}{offer} {validity} "
        "El domicilio se cotiza por separado y la promoción no es acumulable con otras promociones. "
        "Respeta el catálogo, la disponibilidad y los controles normales del pedido. "
        "Pedir productos por esta promoción no autoriza mensajes promocionales futuros."
    )
