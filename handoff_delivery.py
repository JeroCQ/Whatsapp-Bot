"""Durable handoff delivery with optimistic atomic claims and bounded recovery.

Only reservations that never reached conversation creation may be recovered.
An interrupted or ambiguous create is held for human reconciliation, not retried.
"""
import uuid
from datetime import datetime, timedelta, timezone

from config import config
from database import supabase
from follow_up_policy import parse_timestamp
from provider_errors import sanitize_text

MAX_ATTEMPTS = 3


def _table(phone=None):
    query = supabase.table("handoff_deliveries").select("*").eq("business_id", config.BUSINESS_ID)
    return query.eq("phone_number", phone) if phone else query


def _update(phone, request_id, values, *, status=None, attempts=None):
    values = {**values, "updated_at": datetime.now(timezone.utc).isoformat()}
    query = supabase.table("handoff_deliveries").update(values).eq("business_id", config.BUSINESS_ID).eq("phone_number", phone).eq("request_id", request_id)
    if status:
        query = query.eq("status", status)
    if attempts is not None:
        query = query.eq("attempts", attempts)
    rows = query.execute().data or []
    return rows[0] if rows else None


def ensure_request(phone):
    now = datetime.now(timezone.utc).isoformat()
    supabase.table("handoff_deliveries").upsert({"business_id": config.BUSINESS_ID, "phone_number": phone,
        "request_id": uuid.uuid4().hex, "status": "pending", "retry_at": now},
        on_conflict="business_id,phone_number", ignore_duplicates=True).execute()
    rows = _table(phone).execute().data or []
    row = rows[0] if rows else None
    if row and row["status"] in {"created", "cancelled"}:
        # Called only when paused with no active conversation: this is a new
        # handoff after a previously closed/reset conversation.
        return _update(phone, row["request_id"], {"request_id": uuid.uuid4().hex,
            "status": "pending", "attempts": 0, "conversation_id": None, "last_error": None,
            "retry_at": now}, status=row["status"])
    return row


def reserve_request(phone, row, *, now=None):
    now = now or datetime.now(timezone.utc)
    due = parse_timestamp(row.get("retry_at")) or now
    if row["status"] == "reserved" and due <= now:
        row = _update(phone, row["request_id"], {"status": "pending"}, status="reserved") or row
    if row["status"] == "sending":
        if due <= now:
            _update(phone, row["request_id"], {"status": "unknown", "last_error": "worker_interrupted_during_create"}, status="sending")
        return None
    if row["status"] == "pending" and row["attempts"] >= MAX_ATTEMPTS:
        _update(phone, row["request_id"], {"status": "exhausted"}, status="pending", attempts=row["attempts"])
        return None
    if row["status"] != "pending" or due > now:
        return None
    return _update(phone, row["request_id"], {"status": "reserved", "attempts": row["attempts"] + 1,
        "retry_at": (now + timedelta(minutes=3)).isoformat()}, status="pending", attempts=row["attempts"])


def mark_creating(phone, row):
    return _update(phone, row["request_id"], {"status": "sending"}, status="reserved", attempts=row["attempts"])


def complete_request(phone, row, conversation_id):
    return _update(phone, row["request_id"], {"status": "created", "conversation_id": conversation_id, "last_error": None}, status="sending")


def fail_request(phone, row, error, *, ambiguous=False):
    status = "unknown" if ambiguous else ("exhausted" if row["attempts"] >= MAX_ATTEMPTS else "pending")
    return _update(phone, row["request_id"], {"status": status, "last_error": sanitize_text(error),
        "retry_at": (datetime.now(timezone.utc) + timedelta(seconds=60 * row["attempts"])).isoformat()})


def get_request(phone):
    rows = _table(phone).execute().data or []
    return rows[0] if rows else None


def cancel_request(phone, request_id):
    return _update(phone, request_id, {"status": "cancelled"})


def pending_requests(limit=100, *, actionable_only=False):
    statuses = ["pending", "reserved", "sending"] if actionable_only else ["pending", "reserved", "sending", "unknown", "exhausted"]
    return _table().in_("status", statuses).order("retry_at").limit(min(limit, 100)).execute().data or []
