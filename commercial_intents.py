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
    (r"\bmayorista(?:s)?\b|\bventa(?:s)?\s+al\s+por\s+mayor\b", "venta al por mayor"),
    (r"\brevendedor(?:a|es)?\b", "reventa"),
    (r"\bcompra\s+empresarial\b", "compra empresarial"),
    (r"\bgrandes\s+cantidades?\s+para\s+(?:mi\s+)?negocio\b", "grandes cantidades para negocio"),
)


def b2b_reason(text: str) -> str | None:
    """Return a specific, stable reason for high-value commercial opportunities."""
    normalized = _normal(text)
    for pattern, label in _B2B_PATTERNS:
        if re.search(pattern, normalized):
            return f"B2B_HIGH_VALUE: interés en {label}"
    return None


def b2b_actions(text: str, available_file_ids) -> tuple[str | None, list[str]]:
    """Return the single structured handoff reason and existing distributor asset."""
    reason = b2b_reason(text)
    files = ["catalogo_distibuidores"] if reason and "catalogo_distibuidores" in available_file_ids else []
    return reason, files


@dataclass(frozen=True)
class FollowUpPlan:
    stage: str
    delays_minutes: tuple[int, ...]
    messages: tuple[str, ...]


def follow_up_plan(response: str, requested_files: list[str], model_message: str = "") -> FollowUpPlan:
    """Choose the most advanced applicable retail stage; catalog wins the same turn."""
    text = _normal(f"{response} {model_message}")
    if re.search(r"\b(manana|siguiente dia habil|proximo dia habil|el lunes)\b", text):
        message = model_message or "Hola 😊 Ya estamos atendiendo. ¿Retomamos lo que querías pedir?"
        return FollowUpPlan("DEFERRED_DELIVERY", (1440,), (message,))
    if requested_files:
        message = "¿Viste alguna opción que te gustara? Dime si prefieres panadería o postres y te recomiendo dos opciones con precio 😊"
        return FollowUpPlan("CATALOG_SENT", (15, 120, 1440), (message, message, message))
    if re.search(r"\b(comprobante|transferencia|pago|metodo de pago|total (?:es|de))\b", text):
        message = model_message or "¿Te ayudo a dejarlo confirmado? Apenas completes el pago o me confirmes el método, seguimos con tu pedido 😊"
        return FollowUpPlan("PAYMENT_PENDING", (8, 30, 120), (message, message, message))
    if model_message or re.search(r"\b(barrio|ciudad|domicilio|recog(?:er|ida)|cantidad|producto|combo)\b", text):
        message = model_message or "Te dejo adelantado lo que elegiste 😊 Solo me falta el siguiente dato para confirmar el total. ¿Me lo compartes?"
        return FollowUpPlan("INCOMPLETE_INTENT", (10, 45, 180), (message, message, message))
    message = "Hola 😊 ¿Quieres que te ayude a elegir la opción que mejor se ajuste a lo que buscas?"
    return FollowUpPlan("FIRST_RESPONSE", (10, 90, 1440), (message, message, message))


def is_terminal_customer_message(text: str) -> bool:
    """Recognize explicit opt-out/rejection and sale-completion signals."""
    return bool(re.search(
        r"\b(no\s+gracias|no\s+me\s+interesa|no\s+(?:me\s+)?(?:escriban|contacten)|"
        r"deja\s+de\s+(?:escribir|contactar)|(?:ya\s+)?pague|pago\s+realizado|"
        r"(?:envio|adjunto|mando|comparto).{0,20}comprobante|hice\s+la\s+transferencia|pedido\s+confirmado|"
        r"compra\s+confirmada|venta\s+cerrada)\b",
        _normal(text),
    ))
