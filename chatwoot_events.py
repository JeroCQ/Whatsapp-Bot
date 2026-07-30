"""Pure helpers for identifying and routing Chatwoot webhook events."""

import hashlib
import json


SUPPORTED_CHATWOOT_EVENTS = {"conversation_status_changed", "message_created"}


def conversation_id(data: dict):
    conversation = data.get("conversation") or {}
    if isinstance(conversation, dict) and conversation.get("id") is not None:
        return conversation["id"]
    return data.get("id")


def event_id(data: dict) -> str:
    """Build an ID that cannot collide across event types for one conversation.

    Chatwoot uses the conversation ID as ``id`` on status events, so using that
    field alone causes a prior webhook to suppress every later resolution event.
    Message IDs are already unique. Status events use a stable payload digest so
    duplicate deliveries are ignored while a later state transition remains new.
    """
    event = str(data.get("event") or "unknown")
    if event == "message_created":
        message_id = data.get("id") or data.get("message_id")
        if message_id is not None:
            return f"{event}:{message_id}"
    canonical_payload = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    return f"{event}:{digest}"
