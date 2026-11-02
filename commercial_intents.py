"""Deterministic commercial intent and follow-up policy shared by every brand."""

import re
import unicodedata
from dataclasses import dataclass


def _normal(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()


_B2B_PATTERNS = (
    (r"\bmaquil(?:a|ar|an|amos|ado|adora)?\b", "maquila"),
    (r"\b(?:fabricacion|produccion)\s+para\s+terceros\b", "fabricación para terceros"),
    (r"\bmarca\s+blanca\b|\bprivate\s+label\b", "marca blanca/private label"),
    (r"\bdistribu(?:idor(?:a|es)?|cion|ir)\b", "distribución"),
    (r"\bmayorista(?:s)?\b|\bal\s+por\s+mayor\b", "venta al por mayor"),
    (r"\brevendedor(?:a|es)?\b", "reventa"),
    (r"\bcompra\s+empresarial\b", "compra empresarial"),
    (r"\bgrandes\s+cantidades?\s+para\s+(?:mi\s+)?negocio\b", "grandes cantidades para negocio"),
)


def b2b_reason(text: str, *, standard_wholesale_self_service: bool = False) -> str | None:
    """Return a specific, stable reason for high-value commercial opportunities."""
    normalized = _normal(text)
    for pattern, label in _B2B_PATTERNS:
        if re.search(pattern, normalized):
            if standard_wholesale_self_service and label in {"venta al por mayor", "distribución", "reventa", "grandes cantidades para negocio"}:
                continue
            return f"B2B_HIGH_VALUE: interés en {label}"
    return None


def b2b_actions(text: str, available_file_ids, *, standard_wholesale_self_service: bool = False) -> tuple[str | None, list[str]]:
    """Return the single structured handoff reason and existing distributor asset."""
    reason = b2b_reason(text, standard_wholesale_self_service=standard_wholesale_self_service)
    files = ["catalogo_distibuidores"] if reason and "catalogo_distibuidores" in available_file_ids else []
    return reason, files


@dataclass(frozen=True)
class FollowUpPlan:
    stage: str
    delays_minutes: tuple[int, ...]
    messages: tuple[str, ...]


DEFAULT_CATALOG_FOLLOW_UP_MESSAGE = (
    "¿Pudiste revisar las opciones que te compartí? "
    "Cuéntame cuál te interesa y te ayudo a continuar 😊"
)
_LEGACY_CATALOG_FOLLOW_UP_MESSAGE = (
    "¿Viste alguna opción que te gustara? Dime si prefieres panadería o postres "
    "y te recomiendo dos opciones con precio 😊"
)


def normalize_follow_up_message(message: str) -> str:
    """Replace the obsolete shared template, including text in existing RQ jobs."""
    if message.strip() == _LEGACY_CATALOG_FOLLOW_UP_MESSAGE:
        return DEFAULT_CATALOG_FOLLOW_UP_MESSAGE
    return message


def human_control_active(state):
    return bool(state and (state.get("is_paused") or state.get("chatwoot_conversation_id") or state.get("current_state") == "HUMAN_HANDOFF"))


def customer_closes_without_pending_request(customer_message, state=None):
    """Only a standalone closing acknowledgement can end an idle conversation."""
    state = state or {}
    if human_control_active(state):
        return False
    pending = state.get("current_state") in {"PRODUCT_SELECTION", "FORMAT_SELECTION", "QUANTITY_SELECTION", "PAYMENT_PENDING", "PICKUP_INSTRUCTIONS"} or bool(state.get("pending_order"))
    summary = _normal(str(state.get("order_summary") or ""))
    pending = pending or bool(summary and "sin productos confirmados" not in summary)
    text = _normal(customer_message).strip(" ¡!¿?.,")
    acknowledgement = re.fullmatch(
        r"(?:(?:ok|listo|perfecto)[,\s]+)?(?:muchas\s+)?gracias"
        r"(?:\s+por\s+(?:(?:la|tu|su)\s+)?(?:informacion|ayuda|atencion|todo))?"
        r"|hasta luego|chao|adios", text)
    return bool(acknowledgement and not pending)


def follow_up_suppression_reason(customer_message="", *, state=None, handoff_requested=False, model_message=None):
    state = state or {}
    if handoff_requested or human_control_active(state):
        return "human_control"
    if state.get("follow_up_suppressed"):
        return state.get("follow_up_suppression_reason") or "contact_suppressed"
    text = _normal(customer_message).strip(" ¡!¿?.,")
    if is_terminal_customer_message(customer_message):
        return "terminal_customer_signal"
    if re.search(r"\b(?:yo\s+)?(?:te|les|le)\s+(?:aviso|escribo|confirmo)\b|\bno\s+insistas?\b", text):
        return "customer_will_initiate"
    if model_message is not None and not str(model_message).strip():
        return "explicit_empty_follow_up"
    if customer_closes_without_pending_request(customer_message, state):
        return "conversation_closed"
    return None


def follow_up_preference_patch(text, state):
    """Keep explicit contact decisions across acknowledgements and worker restarts."""
    normalized = _normal(text)
    if re.search(r"\bno\s+gracias\b|\bno\s+me\s+interesa\b", normalized):
        return {"follow_up_suppressed": True, "follow_up_suppression_reason": "customer_rejected"}
    if normalized.strip(" ¡!¿?.,") in {"hasta luego", "chao", "adios"}:
        return {"follow_up_suppressed": True, "follow_up_suppression_reason": "conversation_closed"}
    if re.search(r"\b(?:no\s+(?:me\s+)?(?:escriban|contacten)|deja\s+de\s+(?:escribir|contactar)|no\s+insistas?)\b", normalized):
        return {"follow_up_suppressed": True, "follow_up_suppression_reason": "no_contact_requested"}
    if re.search(r"\b(?:yo\s+)?(?:te|les|le)\s+(?:aviso|escribo|confirmo)\b", normalized):
        return {"follow_up_suppressed": True, "follow_up_suppression_reason": "customer_will_initiate"}
    if state.get("follow_up_suppressed"):
        consent = re.search(r"\b(?:puedes|pueden)\s+(?:escribirme|contactarme|avisarme)|\b(?:avisame|recuerdame|retomemos)\b", normalized)
        active_purchase = re.search(r"\b(?:quiero|necesito|comprar|pido|cotiza(?:me)?|tienes|tienen|precio|catalogo)\b", normalized)
        if consent or (state.get("follow_up_suppression_reason") != "no_contact_requested" and active_purchase):
            return {"follow_up_suppressed": False, "follow_up_suppression_reason": None}
    return {}


def catalog_may_suppress_handoff(text, *, deterministic_handoff_reason=None, model_reason=""):
    """Only a simple catalog request can replace an unnecessary model handoff."""
    if deterministic_handoff_reason or b2b_reason(text):
        return False
    if model_reason and model_reason != "Transferencia por IA" and not re.search(r"catalogo|archivo|lista de precios", _normal(model_reason)):
        return False
    normalized = _normal(text)
    if re.search(r"\b(asesor|humano|reclamo|comprobante|pague|bloques?|libras?|kilos?|personaliz|validar)\b", normalized):
        return False
    return bool(re.fullmatch(r"[\s¿?¡!.,]*(?:(?:hola|buenas)[\s,]*)?(?:(?:me|nos)\s+)?(?:(?:envia(?:me)?|manda(?:me)?|comparte(?:me)?|quiero|necesito|ver|tienes|tienen|dame|por favor)\s+)*(?:el\s+|un\s+|tu\s+|su\s+)?(?:catalogo|catalogos|lista de precios)(?:\s+(?:por favor|otra vez|de nuevo|actualizado))?[\s¿?¡!.,]*", normalized))


def unregistered_presentation_reason(text, approved_instruction):
    """Unknown packaged formats need validation; quantity alone is not B2B."""
    normalized = _normal(text)
    requested = re.search(r"\b(bloques?|bultos?|presentacion(?:es)?)\s+(?:de\s+)?(\d+(?:[.,]\d+)?|cinco)\s*(libras?|lb|kilos?|kg)\b", normalized)
    if not requested:
        return None
    number = "5" if requested[2] == "cinco" else requested[2].replace(",", ".")
    unit = r"(?:libras?|lb)" if requested[3].startswith(("libra", "lb")) else r"(?:kilos?|kg)"
    registered = any(re.search(rf"\b{re.escape(number)}\s*{unit}\b", _normal(line)) for line in approved_instruction.splitlines() if "$" in line and re.search(r"bloque|bulto|presentacion", _normal(line)))
    if not registered:
        return "INFORMATION_VALIDATION: presentación solicitada no registrada; confirmar con el equipo sin afirmar falta de disponibilidad"
    return None


def follow_up_plan(response: str, requested_files: list[str], model_message: str | None = None, *, customer_message="", state=None, handoff_requested=False, not_before=None) -> FollowUpPlan | None:
    """Choose a stage while keeping business-authored text or neutral defaults."""
    if follow_up_suppression_reason(customer_message, state=state, handoff_requested=handoff_requested, model_message=model_message):
        return None
    model_message = normalize_follow_up_message((model_message or "").strip())
    text = _normal(f"{response} {model_message}")
    if not_before is not None:
        message = model_message or "Hola 😊 Retomamos en la fecha que acordamos. ¿Te ayudo a continuar con tu pedido?"
        return FollowUpPlan("DEFERRED_DELIVERY", (-1,), (message,))
    if requested_files:
        message = model_message or DEFAULT_CATALOG_FOLLOW_UP_MESSAGE
        return FollowUpPlan("CATALOG_SENT", (15, 120, -1), (message, message, message))
    if re.search(r"\b(comprobante|transferencia|pago|metodo de pago|total (?:es|de))\b", text):
        message = model_message or "¿Te ayudo a dejarlo confirmado? Apenas completes el pago o me confirmes el método, seguimos con tu pedido 😊"
        return FollowUpPlan("PAYMENT_PENDING", (8, 30, 120), (message, message, message))
    if model_message or re.search(r"\b(barrio|ciudad|domicilio|recog(?:er|ida)|cantidad|producto|combo)\b", text):
        message = model_message or "Te dejo adelantado lo que elegiste 😊 Solo me falta el siguiente dato para confirmar el total. ¿Me lo compartes?"
        return FollowUpPlan("INCOMPLETE_INTENT", (10, 45, 180), (message, message, message))
    message = model_message or "Hola 😊 ¿Quieres que te ayude a elegir la opción que mejor se ajuste a lo que buscas?"
    return FollowUpPlan("FIRST_RESPONSE", (10, 90, -1), (message, message, message))


def is_terminal_customer_message(text: str) -> bool:
    """Recognize explicit opt-out/rejection and sale-completion signals."""
    return bool(re.search(
        r"\b(no\s+gracias|no\s+me\s+interesa|no\s+(?:me\s+)?(?:escriban|contacten)|"
        r"deja\s+de\s+(?:escribir|contactar)|(?:ya\s+)?pague|pago\s+realizado|"
        r"(?:envio|adjunto|mando|comparto).{0,20}comprobante|hice\s+la\s+transferencia|pedido\s+confirmado|"
        r"compra\s+confirmada|venta\s+cerrada)\b",
        _normal(text),
    ))
