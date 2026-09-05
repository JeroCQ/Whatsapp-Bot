"""Build compact, extractive handoff context without inventing customer data."""

from __future__ import annotations

import re


_FIELD_PATTERNS = {
    "Nombre": re.compile(r"(?:nombre(?:\s+y\s+apellido)?\s*[:=-]\s*)([^\n,;]{3,80})", re.I),
    "Documento": re.compile(r"(?:(?:c[eé]dula|cc|documento)\s*[:#=-]?\s*)(\d[\d. -]{4,20})", re.I),
    "Ciudad": re.compile(r"(?:ciudad\s*[:=-]\s*)([^\n,;]{2,60})", re.I),
    "Teléfono": re.compile(r"(?:(?:tel[eé]fono|celular|tel\.?|del\.?)\s*[:#=-]?\s*)(\+?\d[\d -]{6,18})", re.I),
    "Email": re.compile(r"([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})"),
    "Dirección": re.compile(r"(?:direcci[oó]n\s*[:=-]\s*)([^\n;]{4,120})", re.I),
    "Barrio": re.compile(r"(?:barrio|b/)\s*[:=-]?\s*([^\n,;]{2,70})", re.I),
    "Pago": re.compile(r"(?:pago|m[eé]todo de pago)\s*[:=-]?\s*(efectivo|transferencia|nequi|bancolombia)", re.I),
}


def _clean(value: str, limit: int = 120) -> str:
    return re.sub(r"\s+", " ", value).strip(" .,-")[:limit]


def supplied_customer_data(messages: list[dict]) -> dict[str, str]:
    """Return the latest explicitly labelled values supplied by the customer."""
    found: dict[str, str] = {}
    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        for label, pattern in _FIELD_PATTERNS.items():
            match = pattern.search(content)
            if match:
                found[label] = _clean(match.group(1))
        # Customers frequently answer a form without repeating its labels.
        # Preserve unambiguous Colombian address and document/phone pairs too.
        if "Dirección" not in found:
            address = re.search(
                r"\b((?:calle|carrera|cra\.?|kra\.?|avenida|av\.?)\s+[^\n;]{4,100})",
                content,
                re.I,
            )
            if address:
                found["Dirección"] = _clean(address.group(1))
        numbers = re.findall(r"(?<!\d)(\d{7,12})(?!\d)", content)
        if len(numbers) >= 2:
            found.setdefault("Documento", numbers[0])
            found.setdefault("Teléfono", numbers[1])
    return found


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
) -> str:
    data = dict(preserved_data or {})
    data.update(supplied_customer_data(messages))
    data_text = "; ".join(f"{label}: {value}" for label, value in data.items())
    operational_error = next(
        (
            _clean(str(message.get("content") or ""), 500)
            for message in reversed(messages)
            if message.get("role") == "system"
            and str(message.get("content") or "").startswith("ERROR")
        ),
        "ninguno registrado",
    )
    return (
        f"**Resumen de handoff**\n"
        f"**Pedido en curso:** {preserved_order or compact_order_summary(messages)}\n"
        f"**Datos ya suministrados:** {data_text or 'ninguno identificado'}\n"
        f"**Incidente operativo:** {operational_error}"
    )
