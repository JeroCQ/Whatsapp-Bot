"""Only source of inbox classification. No database, I/O or wall-clock access."""

from datetime import datetime, timezone
from typing import Any

BUCKETS = {"awaiting_human", "ai_active", "closed"}
PENDING = {"pending", "reserved", "sending", "unknown", "exhausted"}
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
DELIVERY_RANK = {"accepted": 0, "failed": 1, "delivered": 2, "read": 3}


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
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    quality = set(warnings or [])
    # Delivery facts advance the source watermark, but are not conversational
    # activity: a receipt cannot reopen a case or change an actor's read state.
    receipts: dict[str, str] = {}
    activity = []
    for message in messages:
        metadata = message.get("metadata") or {}
        if message.get("role") == "system" and metadata.get("source") == "crm_delivery":
            command, status = metadata.get("command_id"), metadata.get("delivery_status")
            if isinstance(command, str) and isinstance(status, str) and status in DELIVERY_RANK:
                if DELIVERY_RANK[status] > DELIVERY_RANK.get(receipts.get(command, ""), -1):
                    receipts[command] = status
        else:
            activity.append(message)
    ordered = sorted(activity, key=order)
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
    humans = []
    for message in cycle:
        if message.get("role") != "asesor" or (boundary is not None and order(message) <= boundary):
            continue
        metadata = message.get("metadata") or {}
        status = metadata.get("delivery_status")
        status = status if isinstance(status, str) else None
        command = metadata.get("source_event_id")
        receipt = receipts.get(command) if isinstance(command, str) else None
        # Reconciliation can observe a newer metadata edit before its append-only
        # fact, so do not let an older accepted event erase a known failure/read.
        if receipt and DELIVERY_RANK[receipt] > DELIVERY_RANK.get(status or "", -1):
            status = receipt
        if status not in {"failed", "unknown", "pending", "reserved", "sending"}:
            humans.append(message)
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
    crm = state.get("human_support_provider") == "crm"
    phase = state.get("crm_control_phase")
    case_exists = bool(phase == "human" and state.get("crm_control_owner")) if crm else bool(chatwoot)
    status = None if crm else delivery.get("status")
    waiting_customer = bool(paused and humans and not unanswered and case_exists and status not in PENDING)
    resolved_cycle = bool(
        closed_at is not None and not incoming and not handoffs
        and not (delivery and timestamp(delivery.get("created_at")) > closed_at[0])
        and not (state.get("crm_control_phase") in {"human", "pausing"} and state.get("crm_control_owner"))
    )
    if paused:
        # Honor an already recorded operator resolution, even if an old/missing
        # mutable state still reports paused. This never writes a closure event.
        bucket = "closed" if resolved_cycle else "awaiting_human"
    else:
        # Technical/control events are not a customer reopening the case.
        bucket = "closed" if closed_at is not None and not incoming else "ai_active"
    priority = 4
    reason = "waiting_for_response"
    if bucket == "awaiting_human":
        if waiting_customer:
            reason = "human_waiting_customer"
        elif status in {"unknown", "exhausted"}:
            priority, reason = 1, "handoff_delivery_uncertain"
        elif crm and phase == "pausing":
            priority, reason = 1, "crm_pause_pending_inflight_send"
        elif crm and not case_exists:
            priority, reason = 3, "crm_handoff_unassigned"
        elif not case_exists:
            priority, reason = 2, "paused_without_chatwoot"
        elif status in {"pending", "reserved", "sending"}:
            priority, reason = 3, "handoff_creation_pending"
    else:
        priority, reason = 0, "no_human_attention_required"
    attention = min((order(m)[0] for m in unanswered), default=None)
    if bucket == "awaiting_human" and attention is None and not waiting_customer:
        attention = boundary[0] if boundary else timestamp(state.get("last_interaction"))
        if attention == EPOCH:
            attention = order(cycle[0])[0] if cycle else None
            quality.add("missing_attention_timestamp")
    if bucket != "awaiting_human" or waiting_customer:
        attention = None
    latest = max((order(m)[0] for m in incoming), default=None)
    activity_at = max(
        (order(m)[0] for m in ordered if m.get("role") in {"user", "model", "asesor"}),
        default=EPOCH,
    )
    seconds = max(0, int((timestamp(at) - attention).total_seconds())) if attention else 0
    attention_rank = int(attention.timestamp() * 1000000) if attention else 999999999999999999
    latest_rank = 999999999999999999 - int(latest.timestamp() * 1000000) if latest else 999999999999999999
    result: dict[str, Any] = {
        "classification_version": 3,
        "bucket": bucket,
        "attention_since": attention.isoformat() if attention else None,
        "waiting_seconds": seconds,
        "unanswered_customer_count": len(unanswered),
        "priority": priority,
        "priority_reason": reason,
        "last_incoming_at": latest.isoformat() if latest else None,
        "pending_handoff_at": boundary[0].isoformat() if (
            bucket == "awaiting_human" and not waiting_customer and boundary
            and EPOCH < boundary[0] <= timestamp(at)
            and (not crm or phase in {"waiting", "pausing"})
        ) else None,
        "last_activity_at": activity_at.isoformat() if activity_at != EPOCH else None,
        "last_message_id": max((int(m.get("id", 0)) for m in ordered), default=0),
        "data_quality_warnings": sorted(quality),
        "sort_key": f"{priority:02}:{attention_rank:018}:{latest_rank:018}",
    }
    previous = previous or {}
    release = previous.get("inbox_release")
    if release:
        released_at = timestamp(release["at"])
        version = state.get("crm_control_version")
        newer_control = (
            state.get("is_paused") is True
            and type(version) is int
            and release.get("control_version") is not None
            and version > release["control_version"]
        )
        new_request = newer_control or any(
            m.get("role") == "system"
            and str(m.get("content", "")).startswith("HANDOFF:")
            and timestamp(m.get("created_at")) > released_at
            for m in messages
        ) or bool(
            handoff.get("request_id")
            and handoff.get("request_id") != release.get("handoff_request_id")
            # A retry/status edit of an old request is not a new human request.
            and timestamp(handoff.get("created_at")) > released_at
        )
        if new_request:
            result["bucket"] = "awaiting_human"
        else:
            result["inbox_release"] = release
            if release["kind"] == "dismiss":
                result["bucket"] = previous["bucket"]
            else:
                result["bucket"] = "ai_active"
                result.update(priority=0, priority_reason="no_human_attention_required", attention_since=None,
                              waiting_seconds=0)
    elif previous.get("bucket") in {"awaiting_human", "human_active"} and not resolved_cycle:
        # Reading, replying and source reconciliation never release a human case.
        result["bucket"] = "awaiting_human"
    result["in_human_inbox"] = result["bucket"] in {"awaiting_human", "human_active"} and not result.get("inbox_release")
    if not result["in_human_inbox"] or not paused:
        result["pending_handoff_at"] = None
    return result
