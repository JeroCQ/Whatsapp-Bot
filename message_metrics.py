"""Explicit message attribution for dashboard/CSV audits, never time proximity."""
import json
from collections import Counter
from datetime import datetime, timezone


def metadata_of(row):
    value = row.get("metadata") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def correlated_response_metrics(rows):
    kinds, incoming, answers, seen = Counter(), set(), set(), set()
    legacy = 0
    for row in rows:
        data = metadata_of(row)
        key = row.get("event_key")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        source = data.get("source")
        if source not in {"incoming", "bot_response", "follow_up", "human", "internal_alert", "system"}:
            legacy += 1
            continue
        kinds[source] += 1
        scope = (data.get("business_id"), row.get("phone_number"))
        if source == "incoming" and data.get("source_event_id"):
            incoming.add((*scope, data["source_event_id"]))
        elif source in {"bot_response", "human"}:
            answers.update((*scope, identifier) for identifier in data.get("inbound_message_ids", []))
    return {"message_kinds": dict(kinds), "incoming_with_id": len(incoming),
            "incoming_answered": len(incoming & answers), "incoming_without_linked_reply": len(incoming - answers),
            "legacy_unclassified_messages": legacy}


def _timestamp(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError):
        return None


def correlated_response_latencies(rows):
    incoming, answers = {}, []
    for row in rows:
        data = metadata_of(row)
        scope = (data.get("business_id"), row.get("phone_number"))
        if data.get("source") == "incoming" and data.get("source_event_id"):
            stamp = _timestamp(data.get("received_at") or row.get("created_at"))
            if stamp:
                incoming[(*scope, data["source_event_id"])] = stamp
        elif data.get("source") in {"bot_response", "human"}:
            stamp = _timestamp(row.get("created_at"))
            if stamp:
                answers.append((stamp, scope, data))
    served, bot, any_response = set(), [], []
    for stamp, scope, data in sorted(answers, key=lambda item: item[0]):
        for identifier in data.get("inbound_message_ids", []):
            key = (*scope, identifier)
            if key not in incoming or key in served:
                continue
            delay = (stamp - incoming[key]).total_seconds()
            if delay < 0:
                continue
            served.add(key)
            any_response.append(delay)
            if data["source"] == "bot_response":
                bot.append(delay)
    return bot, any_response
