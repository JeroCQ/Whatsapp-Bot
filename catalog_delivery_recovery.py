"""Find unresolved catalog-delivery failures for safe operator recovery."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re


FILE_ID_RE = re.compile(r"catalogo_[a-z0-9_]+")


@dataclass(frozen=True)
class CatalogRecovery:
    phone_number: str
    catalog_id: str
    failed_at: str
    last_customer_at: str | None
    within_service_window: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def find_catalog_recoveries(rows: list[dict], now: datetime | None = None) -> list[CatalogRecovery]:
    """Return latest failures with no later success or handoff, one per phone/file."""
    now = now or datetime.now(timezone.utc)
    states: dict[tuple[str, str], dict] = {}
    last_customer: dict[str, str] = {}
    for row in rows:
        phone = str(row.get("phone_number") or "")
        role = row.get("role")
        content = str(row.get("content") or "")
        created_at = str(row.get("created_at") or "")
        if role == "user" and phone:
            last_customer[phone] = created_at
        ids = FILE_ID_RE.findall(content)
        if role == "system" and content.startswith("ERROR enviando archivos:"):
            for file_id in ids:
                states[(phone, file_id)] = {"failed_at": created_at, "resolved": False}
        elif role == "system" and content.startswith("Archivos enviados:"):
            for file_id in ids:
                if (phone, file_id) in states:
                    states[(phone, file_id)]["resolved"] = True
        elif role == "system" and content.startswith("HANDOFF:"):
            for key in [key for key in states if key[0] == phone]:
                states[key]["resolved"] = True

    recoveries = []
    for (phone, file_id), state in states.items():
        if not phone or state["resolved"]:
            continue
        customer_at = last_customer.get(phone)
        customer_time = _timestamp(customer_at)
        within_window = bool(customer_time and 0 <= (now - customer_time).total_seconds() < 24 * 60 * 60)
        recoveries.append(CatalogRecovery(phone, file_id, state["failed_at"], customer_at, within_window))
    return sorted(recoveries, key=lambda item: item.failed_at, reverse=True)
