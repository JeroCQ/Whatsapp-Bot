"""In-memory SYNTHETIC source for the shared staging CRM.

No network, no credentials, no source database: rows are generated here and fed to the
normal synchronizer, so buckets are computed by the real classifier. Only the two
explicit synthetic businesses may use it.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from crm_service.source import FIELDS, SourceFailure

BUSINESSES = ("synthetic_one", "synthetic_two")
BASE = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)

# (phone, name, kind, handoff_reason)
CUSTOMERS = {
    "synthetic_one": [
        ("573000000001", "Camila Rojas", "needs_reply", "Cliente pide cotizacion mayorista"),
        ("573000000002", "Andres Pardo", "needs_reply_no_chatwoot", "Reclamo por pedido incompleto"),
        ("573000000003", "Luisa Fernandez", "needs_reply_pending", "Solicita hablar con una persona"),
        ("573000000004", "Mateo Gomez", "in_progress", "Cambio de direccion de entrega"),
        ("573000000005", "Valentina Cruz", "in_progress", "Consulta de factura"),
        ("573000000006", "Sebastian Mora", "ai", None),
        ("573000000007", "Daniela Ortiz", "ai", None),
        ("573000000008", "Julian Vargas", "closed", None),
        ("573000000009", "Paula Medina", "closed", None),
    ],
    "synthetic_two": [
        ("573000000001", "Carolina Silva", "needs_reply", "Producto danado al recibir"),
        ("573000000002", "Felipe Duarte", "needs_reply_no_chatwoot", "Pregunta por devolucion"),
        ("573000000003", "Natalia Pineda", "needs_reply_pending", "Quiere cambiar la talla"),
        ("573000000010", "Ricardo Lozano", "in_progress", "Seguimiento de envio"),
        ("573000000011", "Mariana Soto", "in_progress", "Pago duplicado"),
        ("573000000012", "Esteban Rios", "ai", None),
        ("573000000013", "Isabela Navarro", "ai", None),
        ("573000000014", "Tomas Herrera", "closed", None),
        ("573000000015", "Sofia Castano", "closed", None),
    ],
}


def rows(business: str) -> dict[str, list[dict[str, Any]]]:
    if business not in BUSINESSES:
        raise SourceFailure("fixture_source_only_for_synthetic_businesses")
    customers, states, messages, deliveries = [], [], [], []
    mid = 0
    chatwoot = 9000 if business == "synthetic_one" else 9500

    def msg(phone: str, role: str, content: str, at: datetime, refs: list[str] | None = None) -> str:
        nonlocal mid
        mid += 1
        meta: dict[str, Any] = {"business_id": business}
        if role == "user":
            meta["source_event_id"] = f"{business}:{mid}"
        if refs is not None:
            meta["inbound_message_ids"] = refs
        messages.append(
            {
                "id": mid,
                "phone_number": phone,
                "role": role,
                "content": f"{content} (SYNTHETIC)" if role == "system" else f"SYNTHETIC {content}",
                "created_at": at.isoformat(),
                "metadata": meta,
                "event_key": f"{business}:{mid}",
            }
        )
        return f"{business}:{mid}"

    for index, (phone, name, kind, reason) in enumerate(CUSTOMERS[business]):
        start = BASE - timedelta(hours=3 + index * 5)
        customers.append({"phone_number": phone, "name": f"SYNTHETIC {name}", "created_at": start.isoformat()})
        paused = kind != "ai" and kind != "closed"
        has_cw = kind in {"needs_reply", "needs_reply_pending", "in_progress"}
        states.append(
            {
                "phone_number": phone,
                "current_state": "HUMAN_HANDOFF" if paused else "GREETING",
                "is_paused": paused,
                "handoff_reason": reason,
                "last_interaction": (start + timedelta(minutes=30)).isoformat(),
                "chatwoot_conversation_id": chatwoot + index if has_cw else None,
                "customer_data": {"synthetic": True},
                "order_summary": "SYNTHETIC",
            }
        )
        t = start
        first = msg(phone, "user", "Hola, quisiera informacion de mi pedido", t)
        msg(phone, "model", "Hola, con gusto te ayudo. Un momento por favor", t + timedelta(minutes=1))
        if kind.startswith("needs_reply"):
            msg(phone, "user", f"Necesito ayuda: {reason}", t + timedelta(minutes=5))
            msg(phone, "system", f"HANDOFF: {reason}", t + timedelta(minutes=6))
        elif kind == "in_progress":
            asked = msg(phone, "user", f"Necesito ayuda: {reason}", t + timedelta(minutes=5))
            msg(phone, "system", f"HANDOFF: {reason}", t + timedelta(minutes=6))
            msg(phone, "asesor", "Hola, soy tu asesor, reviso tu caso ahora", t + timedelta(minutes=20), [first, asked])
        elif kind == "ai":
            msg(phone, "user", "Gracias, y cual es el horario de atencion?", t + timedelta(minutes=5))
            msg(phone, "model", "Atendemos de lunes a sabado de 8am a 6pm", t + timedelta(minutes=6))
        else:
            msg(phone, "user", "Perfecto, muchas gracias", t + timedelta(minutes=5))
            msg(phone, "system", "RESOLVED: consulta resuelta", t + timedelta(minutes=7))
        if kind == "needs_reply_pending":
            deliveries.append(
                {
                    "business_id": business,
                    "phone_number": phone,
                    "request_id": f"{business}-pending-{index}",
                    "status": "pending",
                    "attempts": 0,
                    "retry_at": t.isoformat(),
                    "conversation_id": None,
                    "last_error": None,
                    "created_at": (t + timedelta(minutes=6)).isoformat(),
                    "updated_at": (t + timedelta(minutes=6)).isoformat(),
                }
            )
        elif paused and has_cw:
            deliveries.append(
                {
                    "business_id": business,
                    "phone_number": phone,
                    "request_id": f"{business}-created-{index}",
                    "status": "created",
                    "attempts": 1,
                    "retry_at": t.isoformat(),
                    "conversation_id": chatwoot + index,
                    "last_error": None,
                    "created_at": (t + timedelta(minutes=6)).isoformat(),
                    "updated_at": (t + timedelta(minutes=6)).isoformat(),
                }
            )
    return {
        "customers": customers,
        "conversation_states": states,
        "message_logs": messages,
        "handoff_deliveries": deliveries,
    }


class FixtureReader:
    def __init__(self, business: str):
        self.data = rows(business)
        self.warnings: set[str] = set()

    def close(self) -> None:
        pass

    def page(self, table: str, *, after: str | int | None, limit: int) -> list[dict[str, Any]]:
        if table not in FIELDS or not 1 <= limit <= 1000:
            raise SourceFailure("fixture_source_table_or_limit_forbidden")
        key = "id" if table == "message_logs" else "phone_number"
        items = sorted(self.data.get(table, []), key=lambda r: r[key])
        if after is not None:
            items = [r for r in items if r[key] > after]
        return [{k: r.get(k) for k in FIELDS[table]} for r in items[:limit]]
