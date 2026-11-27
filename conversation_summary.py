"""Build compact, extractive handoff context without inventing customer data."""

from __future__ import annotations

import re


_FIELD_PATTERNS = {
    "Nombre": re.compile(r"\b(?:nombre(?:\s+y\s+apellidos?)?\s*(?:es |[:=-]\s*)|me llamo\s+)([^\n,;]{3,80})", re.I),
    "Documento": re.compile(r"\b(?:c[eé]dula|cc|documento)\s*(?:es\s+)?[:#=-]?\s*(\d[\d. -]{4,20})", re.I),
    "Ciudad": re.compile(r"\bciudad\s*(?:es |[:=-]\s*)([^\n,;]{2,60})", re.I),
    "Teléfono": re.compile(r"(?:(?:tel[eé]fono|celular|tel\.?|del\.?)\s*[:#=-]?\s*)(\+?\d[\d -]{6,18})", re.I),
    "Email": re.compile(r"([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})"),
    "Dirección": re.compile(r"\bdirecci[oó]n\s*(?:es |[:=-]\s*)([^\n,;]{4,120})", re.I),
    "Barrio": re.compile(r"\b(?:barrio|b/)\s*(?:es\s+)?[:=-]?\s*([^\n,;]{2,70})", re.I),
    "Indicaciones": re.compile(r"\b(?:indicaciones(?: de entrega)?|referencia de entrega)\s*[:=-]\s*([^\n;]{3,120})", re.I),
    "Pago": re.compile(r"(?:pago|m[eé]todo de pago)\s*[:=-]?\s*(efectivo|transferencia|nequi|bancolombia)", re.I),
}


def _clean(value: str, limit: int = 120) -> str:
    return re.sub(r"\s+", " ", value).strip(" .,-")[:limit]


def supplied_customer_data(messages: list[dict]) -> dict[str, str]:
    """Return the latest explicitly labelled values supplied by the customer."""
    return {field: fact["value"] for field, fact in supplied_customer_facts(messages).items()}


def supplied_customer_facts(messages: list[dict]) -> dict[str, dict]:
    found = {}
    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        if re.search(r"\b(?:para esta vez|solo para (?:este pedido|esta entrega)|recibe (?:mi |un |una )|destinatario[: ])", content, re.I):
            continue
        for label, pattern in _FIELD_PATTERNS.items():
            match = pattern.search(content)
            if not match:
                continue
            value = _clean(match.group(1))
            if label == "Nombre" and not re.fullmatch(r"[^\W\d_]+(?:[ '-][^\W\d_]+){0,5}", value):
                continue
            if label in {"Documento", "Teléfono"} and not 5 <= len(re.sub(r"\D", "", value)) <= 15:
                continue
            if value:
                found[label] = {"value": value, "message_id": message.get("id"), "at": message.get("created_at")}
    return found


def merge_customer_facts(data: dict, facts: dict) -> dict:
    merged = dict(data)
    evidence = dict(merged.get("_checkout_evidence") or {})
    for field, fact in facts.items():
        old = evidence.get(field) or {}
        if (fact.get("message_id") or 0) >= (old.get("message_id") or 0):
            merged[field] = fact["value"]
            evidence[field] = {key: fact[key] for key in ("message_id", "at")}
    if evidence:
        merged["_checkout_evidence"] = evidence
    return merged


def redact_checkout_text(value: str) -> str:
    cleaned = value
    for field, pattern in _FIELD_PATTERNS.items():
        if field != "Pago":
            cleaned = pattern.sub(f"[{field} protegido]", cleaned)
    cleaned = re.sub(r"https?://\S+|@bold\d+|\b\d[\d. -]{6,}\d\b", "[dato protegido]", cleaned, flags=re.I)
    return cleaned


def compact_order_summary(messages: list[dict]) -> str:
    """Extract a short product/total summary from the latest checkout response."""
    for message in reversed(messages):
        content = str(message.get("content") or "")
        if message.get("role") not in {"model", "asesor"} or not re.search(
            r"resumen (?:de (?:tu|la) )?(?:orden|pedido)|total(?: a pagar| a transferir| productos)?\s*:",
            content,
            re.I,
        ):
            continue
        useful = []
        for line in content.splitlines():
            cleaned = _clean(line.lstrip("•*- "), 100)
            if cleaned and re.search(r"\$[\d.]|\btotal\b", cleaned, re.I) and not re.search(
                r"domicilio|datos|registrad", cleaned, re.I
            ):
                useful.append(cleaned)
        if useful:
            return _clean("; ".join(useful[-4:]), 260)

    for message in reversed(messages):
        if message.get("role") == "user":
            content = _clean(str(message.get("content") or ""), 180)
            if re.search(r"\b(?:quiero|pedido|llevo|env[ií]ame|un paquete|\d+\s+\w+)", content, re.I):
                return content
    return "Sin productos confirmados todavía"


def build_handoff_summary(
    messages: list[dict],
    preserved_data: dict[str, str] | None = None,
    preserved_order: str | None = None,
    *,
    trigger_is_audio: bool = False,
    trigger_transcript: str | None = None,
) -> str:
    if not trigger_is_audio:
        # Durable recovery can recreate the case without the original media
        # arguments. Recover its explicitly recorded cause, never infer one
        # from whichever audio message happens to be nearby in the history.
        for message in reversed(messages):
            if message.get("role") != "system":
                continue
            if str(message.get("content") or "").startswith("RESOLVED:"):
                break
            metadata = message.get("metadata")
            if isinstance(metadata, dict) and metadata.get("handoff_trigger") == "audio":
                trigger_is_audio = True
                trigger_transcript = metadata.get("trigger_transcript")
                break
    data = dict(preserved_data or {})
    data.update(supplied_customer_data(messages))
    data_text = "; ".join(label for label in data if label in _FIELD_PATTERNS)
    operational_error = next(
        (
            _clean(str(message.get("content") or ""), 500)
            for message in reversed(messages)
            if message.get("role") == "system"
            and str(message.get("content") or "").startswith("ERROR")
        ),
        "ninguno registrado",
    )
    audio_origin = ""
    if trigger_is_audio:
        if isinstance(trigger_transcript, str) and trigger_transcript.strip():
            # Keep the complete instruction supplied in the voice note. In
            # particular, names, dates and personalized text must not be lost
            # to the compact order-summary truncation.
            audio_origin = (
                "\n**Origen de la derivación:** nota de voz del cliente, interpretada mediante transcripción automática."
                "\n**Transcripción automática que motivó la derivación:**\n"
                f"{trigger_transcript.strip()}"
            )
        else:
            audio_origin = (
                "\n**Mensaje recibido:** nota de voz del cliente."
                "\n**Transcripción automática:** no fue posible transcribir esta nota de voz; "
                "no se dispone de su contenido en texto."
            )
    return (
        f"**Resumen de handoff**\n"
        f"**Pedido en curso:** {preserved_order or compact_order_summary(messages)}\n"
        f"**Datos ya suministrados:** {data_text or 'ninguno identificado'}\n"
        f"**Incidente operativo:** {operational_error}"
        f"{audio_origin}"
    )


def format_handoff_history(messages: list[dict]) -> str:
    """Render stored messages without presenting a transcription as typed text."""
    role_icons = {"user": "👤", "model": "🤖", "asesor": "🧑‍💼", "system": "⚠️"}
    rendered = []
    for message in messages:
        role = message.get("role")
        content = str(message.get("content") or "")
        metadata = message.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        if role == "user":
            if metadata.get("is_transcription") is True and metadata.get("input_type", "audio") == "audio":
                content = f"🎙️ [Transcripción automática de nota de voz] {content}"
            elif metadata.get("input_type") == "audio":
                content = f"🎙️ [Nota de voz] {content}"
        rendered.append(f"{role_icons.get(role, '❔')}: {content}")
    return "\n".join(rendered)
