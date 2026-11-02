"""Only source of inbox classification. No database, I/O or wall-clock access."""

from datetime import datetime, timezone
from typing import Any

BUCKETS = {"awaiting_human", "human_active", "ai_active", "closed"}
PENDING = {"pending", "reserved", "sending", "unknown", "exhausted"}
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if not value:
        return EPOCH
    try:
        return timestamp(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return EPOCH


def order(message: dict[str, Any]) -> tuple[datetime, int]:
    return timestamp(message.get("created_at")), int(message.get("id", 0))


def classify(
    state: dict[str, Any],
    messages: list[dict[str, Any]],
    handoff: dict[str, Any],
    at: datetime,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    quality = set(warnings or [])
    ordered = sorted(messages, key=order)
    closed_at = max(
        (
            order(m)
            for m in ordered
            if m.get("role") == "system" and str(m.get("content", "")).startswith("RESOLVED:")
        ),
        default=None,
    )
    cycle = [m for m in ordered if closed_at is None or order(m) > closed_at]
    incoming = [m for m in cycle if m.get("role") == "user"]
    handoffs = [
        m for m in cycle if m.get("role") == "system" and str(m.get("content", "")).startswith("HANDOFF:")
    ]
    boundary = order(handoffs[-1]) if handoffs else None
    # The bot reuses the handoff row for later request_ids without resetting created_at.
    # An update after the current close can therefore represent a NEW request.
    delivery_time = max(timestamp(handoff.get("created_at")), timestamp(handoff.get("updated_at")))
    delivery = handoff if handoff and (closed_at is None or delivery_time > closed_at[0]) else {}
    if delivery and closed_at and timestamp(delivery.get("created_at")) <= closed_at[0]:
        quality.add("handoff_cycle_inferred_from_updated_at")
    if boundary is None and delivery:
        started = timestamp(delivery.get("created_at"))
        boundary = (delivery_time if closed_at and started <= closed_at[0] else started, -1)
    humans = [m for m in cycle if m.get("role") == "asesor" and (boundary is None or order(m) > boundary)]
    # Index responses once instead of comparing every inbound with every human message.
    covered: set[str] = set()
    legacy_latest = None
    all_latest = max((order(m) for m in humans), default=None)
    for human in humans:
        refs = (human.get("metadata") or {}).get("inbound_message_ids")
        if isinstance(refs, list) and refs:
            covered.update(ref for ref in refs if isinstance(ref, str))
        else:
            current = order(human)
            legacy_latest = max(legacy_latest, current) if legacy_latest else current
    unanswered = []
    for customer in incoming:
        event = (customer.get("metadata") or {}).get("source_event_id")
        response_latest = legacy_latest if event else all_latest
        if response_latest:
            quality.add("legacy_response_order_fallback")
        answered = (isinstance(event, str) and event in covered) or bool(
            response_latest and response_latest > order(customer)
        )
        if not answered:
            unanswered.append(customer)
    paused = state.get("is_paused")
    if not isinstance(paused, bool):
        quality.add("missing_is_paused_conservative_attention")
        paused = True
    chatwoot = state.get("chatwoot_conversation_id")
    status = delivery.get("status")
    if paused:
        bucket = (
            "awaiting_human"
            if (unanswered or not humans or status in PENDING or not chatwoot)
            else "human_active"
        )
    else:
        bucket = "closed" if closed_at is not None and not cycle else "ai_active"
    priority = 4
    reason = "waiting_for_response"
    if bucket == "awaiting_human":
        if status in {"unknown", "exhausted"}:
            priority, reason = 1, "handoff_delivery_uncertain"
        elif not chatwoot:
            priority, reason = 2, "paused_without_chatwoot"
        elif status in {"pending", "reserved", "sending"}:
            priority, reason = 3, "handoff_creation_pending"
    else:
        priority, reason = 0, "no_human_attention_required"
    attention = min((order(m)[0] for m in unanswered), default=None)
    if bucket == "awaiting_human" and attention is None:
        attention = boundary[0] if boundary else timestamp(state.get("last_interaction"))
        if attention == EPOCH:
            attention = order(cycle[0])[0] if cycle else None
            quality.add("missing_attention_timestamp")
    if bucket != "awaiting_human":
        attention = None
    latest = max((order(m)[0] for m in incoming), default=None)
    seconds = max(0, int((timestamp(at) - attention).total_seconds())) if attention else 0
    attention_rank = int(attention.timestamp() * 1000000) if attention else 999999999999999999
    latest_rank = 999999999999999999 - int(latest.timestamp() * 1000000) if latest else 999999999999999999
    return {
        "bucket": bucket,
        "attention_since": attention.isoformat() if attention else None,
        "waiting_seconds": seconds,
        "unanswered_customer_count": len(unanswered),
        "priority": priority,
        "priority_reason": reason,
        "last_incoming_at": latest.isoformat() if latest else None,
        "last_message_id": max((int(m.get("id", 0)) for m in ordered), default=0),
        "data_quality_warnings": sorted(quality),
        "sort_key": f"{priority:02}:{attention_rank:018}:{latest_rank:018}",
    }
