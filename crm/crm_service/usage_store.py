"""One idempotent store for Admin ingestion and the scoped server-side pilot sink."""
import hashlib
import json
from typing import Any
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from crm_service.central_schemas import AttemptInput, TurnInput
from crm_service.classification import timestamp
from crm_service.models import ModelUsageAttempt, UsageTurn, now
from crm_service.sync import lock_id

def digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def lock(session: Session, key: str) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id("central:" + key)})

def turn(session: Session, business: str, turn_id: str, conversation: str) -> UsageTurn:
    lock(session, f"usage:{business}:{turn_id}")
    row = session.get(UsageTurn, (business, turn_id))
    if row is None:
        row = UsageTurn(business_id=business, turn_id=turn_id, conversation_id=conversation, outcome="unknown")
        session.add(row)
        session.flush()
    if row.conversation_id != conversation:
        raise HTTPException(409, "usage_turn_identity_conflict")
    return row


def record_turn(session: Session, body: TurnInput) -> dict[str, Any]:
    row = turn(session, body.business_id, body.turn_id, body.conversation_id)
    if (row.outcome not in {"unknown", body.outcome} or
            (row.started_at and body.started_at and timestamp(row.started_at) != timestamp(body.started_at))):
        raise HTTPException(409, "usage_turn_outcome_conflict")
    row.outcome = body.outcome if body.outcome != "unknown" else row.outcome
    row.started_at = row.started_at or body.started_at
    row.updated_at = now()
    return {"business_id": row.business_id, "turn_id": row.turn_id, "outcome": row.outcome}


def record_attempt(session: Session, body: AttemptInput) -> dict[str, str]:
    lock(session, f"attempt:{body.business_id}:{body.attempt_id}")
    turn(session, body.business_id, body.turn_id, body.conversation_id)
    fingerprint = digest(body.model_dump(mode="json"))
    row = session.get(ModelUsageAttempt, (body.business_id, body.attempt_id))
    if row and row.request_hash != fingerprint:
        raise HTTPException(409, "usage_attempt_identity_conflict")
    if row is None:
        row = ModelUsageAttempt(**body.model_dump(exclude={"conversation_id"}), request_hash=fingerprint)
        session.add(row)
        session.flush()
    return {"business_id": row.business_id, "attempt_id": row.attempt_id, "status": "recorded"}
