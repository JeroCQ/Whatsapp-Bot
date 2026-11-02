"""Bot-only state transitions preserve memory and concurrent operator changes."""

import copy
import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import patch

import pytest


@pytest.fixture
def bot_only_database():
    rows, queries = [], []

    class Query:
        def __init__(self, table):
            assert table == "conversation_states"
            self.filters = {}
            self.values = None

        def update(self, values):
            self.values = values
            return self

        def select(self, columns):
            assert columns == "*"
            return self

        def eq(self, key, value):
            self.filters[key] = value
            return self

        def is_(self, key, value):
            assert value == "null"
            self.filters[key] = None
            return self

        def execute(self):
            queries.append((copy.deepcopy(self.filters), copy.deepcopy(self.values)))
            matches = [row for row in rows if all(row.get(key) == value for key, value in self.filters.items())]
            if self.values is not None:
                for row in matches:
                    row.update(copy.deepcopy(self.values))
            return types.SimpleNamespace(data=copy.deepcopy(matches))

    config = types.SimpleNamespace(
        CHATWOOT_ENABLED=False, BUSINESS_ID="future_business",
        SUPABASE_URL="https://synthetic.supabase.co", SUPABASE_KEY="synthetic-key",
    )
    fake_config = types.ModuleType("config")
    fake_config.config = config
    fake_supabase = types.ModuleType("supabase")
    fake_supabase.Client = object
    fake_supabase.create_client = lambda *args: types.SimpleNamespace(table=lambda table: Query(table))
    fake_observability = types.ModuleType("observability")
    fake_observability.message_metadata = lambda *args: {}
    fake_observability.message_event_key = lambda *args: None
    path = Path(__file__).resolve().parents[1] / "database.py"
    spec = importlib.util.spec_from_file_location("_bot_only_database", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"config": fake_config, "supabase": fake_supabase, "observability": fake_observability}):
        spec.loader.exec_module(module)
    return types.SimpleNamespace(module=module, config=config, rows=rows, queries=queries)


def handoff_state(**values):
    return {
        "phone_number": "CO.SyntheticCustomer", "current_state": "HUMAN_HANDOFF",
        "is_paused": True, "chatwoot_conversation_id": 42, "handoff_reason": "Synthetic handoff",
        "customer_data": {"name": "Synthetic customer", "city": "Synthetic city"},
        "order_summary": "2 synthetic products", "follow_up_suppressed": True,
        "follow_up_suppression_reason": "customer_requested_pause", **values,
    }


@pytest.mark.parametrize("business", ("tanaka", "memos", "velvet", "briosos", "future_business"))
def test_disabled_handoff_pause_does_not_write_to_any_database(bot_only_database, business):
    db = bot_only_database
    db.config.BUSINESS_ID = business

    assert db.module.pause_bot_for_handoff("CO.SyntheticCustomer", "Needs attention") is None
    assert db.queries == []


@pytest.mark.parametrize("business", ("tanaka", "memos", "velvet", "briosos", "future_business"))
def test_disabled_handoff_releases_only_selected_customer_and_preserves_memory(bot_only_database, business):
    db = bot_only_database
    db.config.BUSINESS_ID = business
    state = handoff_state()
    other = handoff_state(phone_number="CO.OtherCustomer")
    db.rows.extend((copy.deepcopy(state), copy.deepcopy(other)))

    normalized = db.module.ensure_bot_only_state(state["phone_number"], state)

    assert normalized["current_state"] == "GREETING"
    assert normalized["is_paused"] is False
    assert normalized["chatwoot_conversation_id"] is None
    assert normalized["handoff_reason"] is None
    for key in ("customer_data", "order_summary", "follow_up_suppressed", "follow_up_suppression_reason"):
        assert normalized[key] == state[key]
    assert db.rows[0] == normalized
    assert db.rows[1] == other
    assert state["is_paused"] is True  # The caller's old snapshot is unchanged.
    assert len(db.queries) == 1


def test_linked_case_keeps_an_existing_checkout_state(bot_only_database):
    db = bot_only_database
    state = handoff_state(current_state="PAYMENT_PENDING")
    db.rows.append(copy.deepcopy(state))

    normalized = db.module.ensure_bot_only_state(state["phone_number"], state)

    assert normalized["current_state"] == "PAYMENT_PENDING"
    assert normalized["is_paused"] is False
    assert normalized["order_summary"] == state["order_summary"]


def test_unlinked_local_handoff_can_resume(bot_only_database):
    db = bot_only_database
    state = handoff_state(chatwoot_conversation_id=None)
    db.rows.append(copy.deepcopy(state))

    assert db.module.ensure_bot_only_state(state["phone_number"], state)["is_paused"] is False
    assert db.queries[0][0]["chatwoot_conversation_id"] is None


@pytest.mark.parametrize("state", (
    None,
    {"is_paused": False, "current_state": "GREETING"},
    {"is_paused": True, "current_state": "PAYMENT_PENDING", "handoff_reason": "Operator hold"},
    {"is_paused": True, "current_state": "SAFETY_PAUSED"},
))
def test_unrelated_pauses_and_active_states_are_preserved_without_writes(bot_only_database, state):
    db = bot_only_database

    assert db.module.ensure_bot_only_state("CO.SyntheticCustomer", state) is state
    assert db.queries == []


def test_enabled_mode_keeps_human_control(bot_only_database):
    db = bot_only_database
    db.config.CHATWOOT_ENABLED = True
    state = handoff_state()

    assert db.module.ensure_bot_only_state(state["phone_number"], state) is state
    assert db.queries == []
    db.rows.append(copy.deepcopy(state))
    db.module.pause_bot_for_handoff(state["phone_number"], "Another reason")
    assert db.rows[0]["is_paused"] is True
    assert db.rows[0]["handoff_reason"] == "Another reason"


def test_legacy_config_object_without_mode_preserves_existing_behavior(bot_only_database):
    db = bot_only_database
    del db.config.CHATWOOT_ENABLED
    state = handoff_state()
    db.rows.append(copy.deepcopy(state))

    assert db.module.ensure_bot_only_state(state["phone_number"], state) is state
    db.module.pause_bot_for_handoff(state["phone_number"], "Legacy handoff")
    assert db.rows[0]["handoff_reason"] == "Legacy handoff"


@pytest.mark.parametrize("changed", (
    {"current_state": "PAYMENT_PENDING", "is_paused": True, "handoff_reason": "Operator hold"},
    {"chatwoot_conversation_id": 99, "handoff_reason": "New case"},
))
def test_concurrent_state_change_cannot_be_overwritten_by_stale_snapshot(bot_only_database, changed):
    db = bot_only_database
    stale = handoff_state()
    current = handoff_state(**changed)
    db.rows.append(copy.deepcopy(current))

    assert db.module.ensure_bot_only_state(stale["phone_number"], stale) == current
    assert db.rows[0] == current
    assert len(db.queries) == 2
    assert db.queries[1][1] is None
