from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from commercial_intents import FollowUpPlan
from follow_up_policy import (resolve_follow_up_schedule, load_business_policy,
    customer_agreed_not_before, contact_window_is_open, operating_window_is_open, parse_timestamp)

TZ = ZoneInfo("America/Bogota")


@pytest.mark.parametrize("hour,minute", [(22, 0), (7, 30), (7, 59)])
def test_night_and_preopening_sequences_coalesce_collisions(hour, minute):
    reference = datetime(2026, 10, 19, hour, minute, tzinfo=TZ)
    plan = FollowUpPlan("FIRST_RESPONSE", (10, 90, -1), ("a", "b", "c"))
    items = resolve_follow_up_schedule(plan, reference, reference + timedelta(hours=24), business_id="memos", environ={})
    assert len({item.send_at for item in items}) == len(items)
    assert sum(item.send_at.hour == 8 and item.send_at.minute == 0 for item in items) == 1
    assert all((b.send_at - a.send_at).total_seconds() >= 900 for a, b in zip(items, items[1:]))


def test_worker_downtime_coalesces_overdue_jobs_without_extending_window():
    reference = datetime(2026, 10, 19, 8, tzinfo=TZ)
    plan = FollowUpPlan("PAYMENT_PENDING", (8, 30, 120), ("a", "b", "c"))
    items = resolve_follow_up_schedule(plan, reference, reference + timedelta(hours=24),
        business_id="memos", now=reference + timedelta(hours=3), environ={})
    assert len(items) == 1
    assert resolve_follow_up_schedule(plan, reference, reference + timedelta(hours=24),
        business_id="memos", now=reference + timedelta(hours=25), environ={}) == []


def test_memos_sunday_and_monday_commitment_are_preserved():
    sunday = datetime(2026, 10, 18, 10, tzinfo=TZ)
    assert not contact_window_is_open(sunday, business_id="memos", environ={})
    assert not operating_window_is_open(sunday, business_id="memos", environ={})
    monday = customer_agreed_not_before("el lunes retomamos", sunday, business_id="memos", environ={})
    assert monday == datetime(2026, 10, 19, 8, tzinfo=TZ)
    plan = FollowUpPlan("DEFERRED_DELIVERY", (-1,), ("Retomamos lo acordado",))
    scheduled = resolve_follow_up_schedule(plan, sunday, sunday + timedelta(hours=24),
        business_id="memos", agreed_not_before=monday, environ={})
    assert [item.send_at for item in scheduled] == [monday]
    saturday = sunday - timedelta(days=1)
    monday = customer_agreed_not_before("el lunes", saturday, business_id="memos", environ={})
    assert resolve_follow_up_schedule(plan, saturday, saturday + timedelta(hours=24),
        business_id="memos", agreed_not_before=monday, environ={}) == []


def test_contact_hours_are_separate_from_memos_operations():
    at_seven = datetime(2026, 10, 19, 7, tzinfo=TZ)
    at_five = at_seven.replace(hour=17)
    assert operating_window_is_open(at_seven, business_id="memos", environ={})
    assert not contact_window_is_open(at_seven, business_id="memos", environ={})
    assert contact_window_is_open(at_five, business_id="memos", environ={})
    assert not operating_window_is_open(at_five, business_id="memos", environ={})


def test_opening_claim_respects_operation_even_when_stage_is_catalog():
    reference = datetime(2026, 10, 19, 16, 25, tzinfo=TZ)
    plan = FollowUpPlan("CATALOG_SENT", (15, 120), ("Ya estamos atendiendo",) * 2)
    scheduled = resolve_follow_up_schedule(plan, reference, reference + timedelta(hours=24), business_id="memos", environ={})
    assert [item.send_at for item in scheduled] == [datetime(2026, 10, 20, 8, tzinfo=TZ)]


def test_policy_is_generic_configurable_and_rejects_cross_brand_paths():
    assert load_business_policy("future_shop", {}).min_spacing_seconds == 900
    assert load_business_policy("future_shop", {"FOLLOW_UP_MIN_SPACING_SECONDS": "1800"}).min_spacing_seconds == 1800
    for business in ("../memos", "", "memos/../tanaka"):
        with pytest.raises(ValueError):
            load_business_policy(business, {})
    assert parse_timestamp(None) is None
    assert parse_timestamp("invalid") is None
    assert parse_timestamp("1792501200").tzinfo is not None
