import importlib
from datetime import datetime, timedelta, timezone
from threading import Lock
from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.fixture
def handoff_store(monkeypatch):
    # The module's DB is never contacted: a CAS adapter simulates the same atomic
    # conditional update also exercised against PostgreSQL in migration tests.
    for key, value in {"BUSINESS_ID": "memos", "SUPABASE_URL": "https://example.supabase.co", "SUPABASE_KEY": "test",
        "WA_VERIFY_TOKEN": "test", "WA_TOKEN": "test", "WA_PHONE_NUMBER_ID": "test", "GEMINI_API_KEY": "test"}.items():
        monkeypatch.setenv(key, value)
    module = importlib.import_module("handoff_delivery")
    now = datetime.now(timezone.utc)
    data = {"request_id": "request", "status": "pending", "attempts": 0, "retry_at": now.isoformat()}
    lock = Lock()
    def update(phone, request_id, values, *, status=None, attempts=None):
        with lock:
            if data["request_id"] != request_id or (status is not None and data["status"] != status) or (attempts is not None and data["attempts"] != attempts):
                return None
            data.update(values)
            return dict(data)
    monkeypatch.setattr(module, "_update", update)
    return module, data, now


def test_handoff_reservation_is_exclusive_between_workers(handoff_store):
    module, data, now = handoff_store
    initial = dict(data)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: module.reserve_request("57300", initial, now=now), range(2)))
    assert sum(result is not None for result in results) == 1
    assert data["attempts"] == 1


def test_interrupted_creation_is_held_for_reconciliation(handoff_store):
    module, data, now = handoff_store
    reserved = module.reserve_request("57300", dict(data), now=now)
    module.mark_creating("57300", reserved)
    assert module.reserve_request("57300", dict(data), now=now + timedelta(minutes=4)) is None
    assert data["status"] == "unknown"
    assert module.reserve_request("57300", dict(data), now=now + timedelta(hours=1)) is None


def test_confirmed_failures_have_bounded_recovery(handoff_store):
    module, data, now = handoff_store
    for attempt in range(3):
        reserved = module.reserve_request("57300", dict(data), now=now + timedelta(minutes=10 * attempt))
        assert reserved is not None
        module.fail_request("57300", reserved, "confirmed pre-create failure")
    assert data["status"] == "exhausted"
    assert module.reserve_request("57300", dict(data), now=now + timedelta(days=1)) is None
