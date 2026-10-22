from concurrent.futures import ThreadPoolExecutor

import fakeredis
import pytest

from follow_up_store import execute, keys


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("BUSINESS_ID", "memos")
    monkeypatch.setenv("WA_PHONE_NUMBER_ID", "meta-memos")
    redis = fakeredis.FakeRedis()
    def call(op, token="sequence", attempt=1, now=100000, **kwargs):
        return execute(redis, op, "CO.TestUser", token, "CATALOG_SENT", attempt, now=now, **kwargs)
    call("register")
    return redis, call


def test_two_workers_cannot_reserve_the_same_attempt(store):
    _, call = store
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: call("reserve"), range(2)))
    assert [r["status"] for r in results].count("reserved") == 1


def test_different_attempts_cannot_send_together_after_restart(store):
    _, call = store
    a, b = call("reserve", attempt=1), call("reserve", attempt=2)
    assert a["status"] == b["status"] == "reserved"
    assert call("begin", reservation_id=a["reservation_id"])["status"] == "sending"
    deferred = call("begin", attempt=2, reservation_id=b["reservation_id"])
    assert deferred["status"] == "deferred"
    assert deferred["retry_at"] == 100960  # Protect even an in-flight/crashed provider request.
    assert call("reserve", attempt=3, now=100010)["status"] == "deferred"


def test_new_sequence_does_not_inherit_old_eight_day_claim(store):
    _, call = store
    assert call("claim")["status"] == "claimed"
    assert call("claim")["status"] == "duplicate"
    call("register", token="new_sequence", now=100001)
    assert call("claim", token="new_sequence", now=100002)["status"] == "claimed"
    assert call("reserve")["status"] == "stale"


def test_pre_send_failure_recovers_but_unknown_result_never_retries(store):
    _, call = store
    reserved = call("reserve")
    failed = call("finish", reservation_id=reserved["reservation_id"], outcome="confirmed_failure")
    assert failed["status"] == "retry"
    assert call("reserve", now=100061)["status"] == "reserved"
    # Use a different attempt for the ambiguous provider request.
    reserved = call("reserve", attempt=2, now=101000)
    assert call("begin", attempt=2, reservation_id=reserved["reservation_id"], now=101000)["status"] == "sending"
    assert call("finish", attempt=2, reservation_id=reserved["reservation_id"], outcome="unknown", now=101001)["status"] == "unknown"
    assert call("reserve", attempt=2, now=103000)["status"] == "unknown"


def test_crash_before_send_is_recoverable_after_lease_but_not_during_send(store):
    _, call = store
    reserved = call("reserve")
    assert call("reserve", now=100010)["status"] == "deferred"
    recovered = call("reserve", now=100061)
    assert recovered["status"] == "reserved"
    assert call("begin", reservation_id=reserved["reservation_id"], now=100062)["status"] == "stale_lease"
    call("begin", reservation_id=recovered["reservation_id"], now=100062)
    assert call("reserve", now=102000)["status"] == "sending"


def test_recovery_is_bounded(store):
    _, call = store
    for index in range(3):
        reserved = call("reserve", now=100000 + index * 61)
        assert reserved["status"] == "reserved"
        failed = call("finish", reservation_id=reserved["reservation_id"], outcome="confirmed_failure", now=100000 + index * 61)
    assert failed["status"] == "exhausted"
    assert call("reserve", now=103000)["status"] == "exhausted"


def test_customer_reply_replacement_and_legacy_tombstone(store):
    redis, call = store
    call("activity", token="inbound-new", now=100100)
    assert call("reserve")["status"] == "stale"
    assert call("register", now=100000)["status"] == "stale"
    redis.set(keys("CO.TestUser")[4], "legacy-token")
    assert call("reserve", token="legacy-token", now=100101)["status"] == "stale"


def test_duplicate_inbound_at_same_second_does_not_cancel_newer_sequence(store):
    _, call = store
    call("activity", token="event-1", now=100100)
    call("activity", token="event-2", now=100100)
    call("register", token="new", now=100100, provider_message_id="event-2")
    assert call("activity", token="event-1", now=100100)["status"] == "unchanged"
    assert call("reserve", token="new", now=100101)["status"] == "reserved"


def test_legacy_sequence_adopts_without_deleting_redis_jobs(monkeypatch):
    monkeypatch.setenv("BUSINESS_ID", "memos")
    redis = fakeredis.FakeRedis()
    redis.set("whatsapp-follow-up-token:57300", "legacy")
    redis.set("rq:job:keep-me", "untouched")
    result = execute(redis, "reserve", "57300", "legacy", "LEGACY", 1, now=100000)
    assert result["status"] == "reserved"
    assert redis.get("rq:job:keep-me") == b"untouched"


def test_already_claimed_legacy_attempt_is_held_without_blocking_new_sequences(monkeypatch):
    redis = fakeredis.FakeRedis()
    redis.set("whatsapp-follow-up-token:57300", "legacy")
    redis.set("whatsapp-follow-up-token:57300:CATALOG_SENT:1", "1")
    assert execute(redis, "reserve", "57300", "legacy", "CATALOG_SENT", 1, now=100000)["status"] == "legacy_claim_ambiguous"
    execute(redis, "register", "57300", "new", now=100001)
    assert execute(redis, "reserve", "57300", "new", "CATALOG_SENT", 1, now=100002)["status"] == "reserved"


def test_business_and_meta_number_isolation(store, monkeypatch):
    redis, call = store
    for business in ("tanaka", "velvet", "future_shop"):
        monkeypatch.setenv("BUSINESS_ID", business)
        assert call("reserve")["status"] == "stale"
    monkeypatch.setenv("BUSINESS_ID", "memos")
    monkeypatch.setenv("WA_PHONE_NUMBER_ID", "other-meta")
    assert call("reserve")["status"] == "stale"
