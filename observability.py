"""Structured, low-PII operational events for handoffs and follow-ups."""

import contextlib
import contextvars
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import config

_trace = contextvars.ContextVar("message_trace", default={})


def current_trace():
    return dict(_trace.get())


def update_trace(**fields):
    _trace.set({**current_trace(), **fields})


@contextlib.contextmanager
def message_trace(**fields):
    trace = current_trace()
    trace.update({key: value for key, value in fields.items() if value is not None})
    token = _trace.set(trace)
    try:
        yield trace
    finally:
        _trace.reset(token)


def deployment_sha():
    return os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GIT_COMMIT_SHA") or "unknown"


def record_provider_message_id(response):
    trace = current_trace()
    trace.pop("provider_message_id", None)
    _trace.set(trace)
    try:
        provider_id = response.json().get("messages", [{}])[0].get("id")
        if provider_id:
            trace = current_trace()
            trace["provider_message_id"] = str(provider_id)
            _trace.set(trace)
            return str(provider_id)
    except (ValueError, AttributeError, IndexError, TypeError):
        pass
    return ""


def message_metadata(role):
    trace = current_trace()
    source = {"user": "incoming", "asesor": "human", "model": "bot_response"}.get(role, "system")
    if role == "model" and trace.get("source") == "follow_up":
        source = "follow_up"
    if role == "system" and trace.get("source") == "internal_alert":
        source = "internal_alert"
    trace.update(source=source, business_id=config.BUSINESS_ID, deployment_sha=deployment_sha())
    if source == "incoming":
        trace.pop("provider_message_id", None)
    return trace


def message_event_key(phone, role, content, metadata):
    source_id = metadata.get("source_event_id")
    sequence = metadata.get("sequence_token")
    if not source_id and not sequence:
        return None
    identity = [config.BUSINESS_ID, phone, role, metadata.get("source"), source_id,
                sequence, metadata.get("attempt"), content]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def operational_event(event: str, *, phone: str, stage: str, reason: str,
                      attempt: int = 0, scheduled_at=None, result: str = "", **fields) -> None:
    observed_at = datetime.now(timezone.utc)
    payload = {
        **current_trace(),
        **fields,
        "event": event,
        "business_id": config.BUSINESS_ID,
        "deployment_sha": deployment_sha(),
        "conversation": phone,
        "stage": stage,
        "reason": reason,
        "attempt": attempt,
        "scheduled_at": scheduled_at.isoformat() if hasattr(scheduled_at, "isoformat") else scheduled_at,
        "result": result,
        "observed_at": observed_at.isoformat(),
        "observed_at_colombia": observed_at.astimezone(ZoneInfo("America/Bogota")).isoformat(),
    }
    identity = [config.BUSINESS_ID, phone, event, stage, reason, result, attempt,
                payload.get("sequence_token"), payload.get("source_event_id"), payload.get("job_id"), payload.get("scheduled_at")]
    identity.append(payload.get("request_id"))
    has_parent = any(payload.get(key) for key in ("sequence_token", "source_event_id", "job_id", "request_id"))
    payload["event_id"] = hashlib.sha256(json.dumps(identity, default=str).encode()).hexdigest() if has_parent else uuid.uuid4().hex
    print(f"[OPERATIONAL_EVENT] {json.dumps(payload, ensure_ascii=False, sort_keys=True)}", flush=True)
