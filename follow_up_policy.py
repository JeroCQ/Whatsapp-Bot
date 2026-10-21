"""Brand-configured calendars and deterministic scheduling of commercial reminders.

The contact calendar controls unsolicited reminders.  The operating calendar
describes when a business can fulfil a promise to resume during working hours.
Neither calendar changes delivery routes, prices or the inbound messaging window.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo


_CLIENTS_ROOT = Path(__file__).resolve().parent / "src" / "clients"
_BUSINESS_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_DEFAULT_HOURS = {"days": list(range(7)), "start": "08:00", "end": "18:00"}


@dataclass(frozen=True)
class WeeklyHours:
    days: tuple[int, ...]
    start: time
    end: time


@dataclass(frozen=True)
class BusinessPolicy:
    timezone: ZoneInfo
    contact_hours: WeeklyHours
    operating_hours: WeeklyHours
    min_spacing_seconds: int
    standard_wholesale_self_service: bool = False


@dataclass(frozen=True)
class ScheduledAttempt:
    attempt: int
    message: str
    send_at: datetime


def parse_timestamp(value, *, default_timezone=timezone.utc) -> datetime | None:
    """Parse a provider timestamp without replacing missing/invalid data with now."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)) or str(value).isdigit():
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=default_timezone) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _hours(value, name: str) -> WeeklyHours:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    try:
        days = tuple(sorted(set(value["days"])))
        start = time.fromisoformat(value["start"])
        end = time.fromisoformat(value["end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {name}") from exc
    if not days or any(type(day) is not int or not 0 <= day <= 6 for day in days):
        raise ValueError(f"{name}.days must contain weekday integers 0 through 6")
    if start.tzinfo or end.tzinfo or start >= end:
        raise ValueError(f"{name} must have a same-day start before end")
    return WeeklyHours(days, start, end)


def load_business_policy(business_id: str, environ: Mapping | None = None) -> BusinessPolicy:
    """Load only this deployment's brand data; unknown brands use neutral defaults.

    BUSINESS_POLICY_JSON overrides local policy for an isolated deployment.  It
    contains calendar data only and does not require sharing another brand's IDs.
    """
    environ = os.environ if environ is None else environ
    business_id = str(business_id or "").strip()
    if not _BUSINESS_ID.fullmatch(business_id):
        raise ValueError("BUSINESS_ID is invalid for loading a business policy")
    raw = str(environ.get("BUSINESS_POLICY_JSON", "") or "").strip()
    if raw:
        document = json.loads(raw)
    else:
        policy_path = _CLIENTS_ROOT / business_id / "business_policy.json"
        document = json.loads(policy_path.read_text(encoding="utf-8")) if policy_path.is_file() else {}
    if not isinstance(document, dict):
        raise ValueError("Business policy must be a JSON object")
    selected_timezone = ZoneInfo(document.get("timezone", "America/Bogota"))
    contact = _hours(document.get("contact_hours", _DEFAULT_HOURS), "contact_hours")
    operating = _hours(document.get("operating_hours", _DEFAULT_HOURS), "operating_hours")
    if not set(contact.days).intersection(operating.days) or max(contact.start, operating.start) >= min(contact.end, operating.end):
        raise ValueError("Contact and operating calendars must overlap at least once a week")
    spacing = int(environ.get("FOLLOW_UP_MIN_SPACING_SECONDS") or document.get("follow_up_min_spacing_seconds", 900))
    if spacing < 1:
        raise ValueError("FOLLOW_UP_MIN_SPACING_SECONDS must be positive")
    self_service = document.get("standard_wholesale_self_service", False)
    if type(self_service) is not bool:
        raise ValueError("standard_wholesale_self_service must be boolean")
    return BusinessPolicy(selected_timezone, contact, operating, spacing, self_service)


def _local(value: datetime, policy: BusinessPolicy) -> datetime:
    # An unqualified business-calendar datetime is interpreted in that business's
    # local time. Provider timestamps should be parsed with parse_timestamp first.
    return value.replace(tzinfo=policy.timezone) if value.tzinfo is None else value.astimezone(policy.timezone)


def _effective_hours(policy: BusinessPolicy, requires_operation: bool) -> WeeklyHours:
    if not requires_operation:
        return policy.contact_hours
    return WeeklyHours(
        tuple(sorted(set(policy.contact_hours.days).intersection(policy.operating_hours.days))),
        max(policy.contact_hours.start, policy.operating_hours.start),
        min(policy.contact_hours.end, policy.operating_hours.end),
    )


def _next_eligible(value: datetime, policy: BusinessPolicy, requires_operation: bool = False) -> datetime:
    current = _local(value, policy)
    hours = _effective_hours(policy, requires_operation)
    for days_ahead in range(8):
        day = current.date() + timedelta(days=days_ahead)
        if day.weekday() not in hours.days:
            continue
        opening = datetime.combine(day, hours.start, tzinfo=policy.timezone)
        closing = datetime.combine(day, hours.end, tzinfo=policy.timezone)
        candidate = max(current, opening)
        if candidate < closing:
            return candidate
    raise ValueError("Business calendar has no eligible contact time")


def next_eligible_contact_at(now: datetime | None = None, *, business_id: str, requires_operation: bool = False, environ: Mapping | None = None) -> datetime:
    policy = load_business_policy(business_id, environ)
    return _next_eligible(now or datetime.now(timezone.utc), policy, requires_operation)


def contact_window_is_open(now: datetime | None = None, *, business_id: str, environ: Mapping | None = None) -> bool:
    policy = load_business_policy(business_id, environ)
    current = _local(now or datetime.now(timezone.utc), policy)
    return current.weekday() in policy.contact_hours.days and policy.contact_hours.start <= current.time() < policy.contact_hours.end


def operating_window_is_open(now: datetime | None = None, *, business_id: str, environ: Mapping | None = None) -> bool:
    policy = load_business_policy(business_id, environ)
    current = _local(now or datetime.now(timezone.utc), policy)
    return current.weekday() in policy.operating_hours.days and policy.operating_hours.start <= current.time() < policy.operating_hours.end


def message_announces_opening(message: str) -> bool:
    text = unicodedata.normalize("NFKD", message or "").encode("ascii", "ignore").decode().lower()
    return bool(re.search(r"\bya\s+(?:(?:estamos|estan|esta)\s+(?:atendiendo|abiert[oa]s?)|abrimos)\b|\bbodega\s+(?:ya\s+)?(?:esta\s+)?(?:abierta|atendiendo)\b", text))


def _next_opening_after(reference: datetime, policy: BusinessPolicy, requires_operation: bool) -> datetime:
    current = _local(reference, policy)
    hours = _effective_hours(policy, requires_operation)
    if current.weekday() in hours.days and current.time() < hours.start:
        return datetime.combine(current.date(), hours.start, tzinfo=policy.timezone)
    tomorrow = datetime.combine(current.date() + timedelta(days=1), time.min, tzinfo=policy.timezone)
    return _next_eligible(tomorrow, policy, requires_operation)


def customer_agreed_not_before(text: str, reference_at: datetime, *, business_id: str, environ: Mapping | None = None) -> datetime | None:
    """Resolve a day mentioned by the customer, never from generated bot prose.

    Callers decide whether the customer actually agreed to defer.  An agreement
    beyond Meta's window is retained here and later omitted by the scheduler;
    it must never be silently moved to an earlier eligible day.
    """
    policy = load_business_policy(business_id, environ)
    current = _local(reference_at, policy)
    normalized = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    day = None
    if re.search(r"\bpasado manana\b", normalized):
        day = current.date() + timedelta(days=2)
    elif re.search(r"\bmanana\b", normalized):
        day = current.date() + timedelta(days=1)
    else:
        weekdays = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
        for weekday, name in enumerate(weekdays):
            if re.search(rf"\b{name}\b", normalized):
                days_ahead = (weekday - current.weekday()) % 7
                day = current.date() + timedelta(days=days_ahead or (7 if re.search(r"\bproximo\b", normalized) else 0))
                break
    if day is None:
        return None
    return _next_eligible(datetime.combine(day, time.min, tzinfo=policy.timezone), policy, True)


def resolve_follow_up_schedule(plan, reference_at: datetime, expires_at, *, business_id: str, agreed_not_before: datetime | None = None, now: datetime | None = None, environ: Mapping | None = None) -> list[ScheduledAttempt]:
    """Resolve a sequence once, coalescing collisions before enforcing spacing.

    Older offsets can converge at the same opening.  A single reminder replaces
    that collision rather than creating a burst after a night or worker outage.
    Expiry always derives from the original inbound event, independently of now.
    """
    policy = load_business_policy(business_id, environ)
    reference = _local(reference_at, policy)
    current = _local(now or reference_at, policy)
    expiry = parse_timestamp(expires_at)
    if expiry is None or current >= expiry:
        return []
    not_before = _local(agreed_not_before, policy) if agreed_not_before is not None else None
    requires_operation = plan.stage == "DEFERRED_DELIVERY" or any(message_announces_opening(message) for message in plan.messages)
    candidates = []
    for attempt, (delay, message) in enumerate(zip(plan.delays_minutes, plan.messages), 1):
        target = _next_opening_after(reference, policy, requires_operation) if delay == -1 else reference + timedelta(minutes=max(1, int(delay)))
        target = max(target, current, not_before) if not_before is not None else max(target, current)
        target = _next_eligible(target, policy, requires_operation)
        if target < expiry:
            candidates.append(ScheduledAttempt(attempt, message, target))

    scheduled = []
    seen_targets = set()
    for candidate in sorted(candidates, key=lambda item: (item.send_at, item.attempt)):
        if candidate.send_at in seen_targets:
            continue
        seen_targets.add(candidate.send_at)
        target = candidate.send_at
        if scheduled:
            target = _next_eligible(max(target, scheduled[-1].send_at + timedelta(seconds=policy.min_spacing_seconds)), policy, requires_operation)
        if target < expiry:
            scheduled.append(ScheduledAttempt(candidate.attempt, candidate.message, target))
    return scheduled
