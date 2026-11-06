"""Small operator/queue policy on existing agents, assignments and classification JSON."""
import hashlib
import unicodedata
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_

from crm_service.models import CRMConversation


def customer_queue(state: dict[str, Any]) -> tuple[str, bool]:
    data = state.get("customer_data") or {}
    if not isinstance(data, dict):
        data = {}
    for field in ("tipo_cliente", "tipo_de_cliente", "customer_type", "client_type", "tipoCliente", "segment"):
        value = unicodedata.normalize("NFKD", str(data.get(field) or "")).encode("ascii", "ignore").decode().strip().lower()
        if value in {"mayorista", "mayoristas", "wholesale", "b2b", "al por mayor"}:
            return "wholesale", False
        if value in {"detal", "retail", "minorista", "consumidor final", "al detal"}:
            return "retail", False
    return "retail", True


def attention_queue(crm: CRMConversation) -> str:
    return str(crm.classification.get("attention_queue") or "retail")


def authorize_case(principal: Any, crm: CRMConversation) -> None:
    principal.authorize(crm.business_id)
    if crm.business_id == "memos" and not principal.is_admin and attention_queue(crm) != (principal.attention_queue or "retail"):
        raise HTTPException(403, "Conversation belongs to another attention queue")


def queue_clause(principal: Any) -> Any:
    if principal.is_admin:
        from sqlalchemy import true
        return true()
    return or_(CRMConversation.business_id != "memos", func.coalesce(
        CRMConversation.classification["attention_queue"].as_string(), "retail"
    ) == (principal.attention_queue or "retail"))


def attention_lock(business: str, phone: str) -> int:
    return int.from_bytes(hashlib.sha256(f"crm-attention:{business}:{phone}".encode()).digest()[:8], "big", signed=True)
