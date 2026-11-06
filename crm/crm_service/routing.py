"""Small operator/queue policy on existing agents, assignments and classification JSON."""
import hashlib
import unicodedata
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_

from crm_service.models import CRMConversation

QUEUE_ROUTING_VERSION = 1


def customer_queue(state: dict[str, Any]) -> tuple[str, bool]:
    # The bot's current B2B handoff is stronger evidence than a missing/stale
    # customer type. Never infer routing from historical messages or free text.
    if state.get("is_paused") is True and str(state.get("handoff_reason") or "").partition(":")[0].strip() == "B2B_HIGH_VALUE":
        return "wholesale", False
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


def project_queue(state: dict[str, Any], previous: dict[str, Any], entering: bool) -> dict[str, Any]:
    """Route an unowned handoff, preserving transfers and newer command authority."""
    version = state.get("crm_control_version")
    version = version if type(version) is int and version >= 0 else None
    checkpoint = previous.get("queue_control_version")
    checkpoint = checkpoint if type(checkpoint) is int else None
    waiting = state.get("is_paused") is True and state.get("crm_control_phase") == "waiting" and not state.get("crm_control_owner")
    newer = waiting and version is not None and checkpoint is not None and version > checkpoint
    repair = waiting and checkpoint is None and not previous.get("queue_transferred")
    reroute = entering or not previous.get("attention_queue") or newer or repair
    if checkpoint is not None and (version is None or version < checkpoint):
        reroute = False  # A stale source snapshot cannot undo a confirmed take/transfer.
    if reroute:
        queue, unknown = customer_queue(state)
    else:
        queue = str(previous.get("attention_queue") or "retail")
        unknown = bool(previous.get("queue_needs_classification", False))
    return {
        "attention_queue": queue,
        "queue_needs_classification": unknown,
        "queue_routing_version": QUEUE_ROUTING_VERSION,
        "queue_control_version": max(v for v in (version, checkpoint, -1) if v is not None),
        "queue_transferred": bool(previous.get("queue_transferred") and not reroute),
    }


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
