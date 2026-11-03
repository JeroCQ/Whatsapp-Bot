from dataclasses import replace

import pytest
from sqlalchemy import event, func, select

from crm_service.models import NotificationEvent, SourceMessage, SyncCursor, SyncRequest, SyncStatus, now
from crm_service.settings import Business, Settings
from crm_service.sync import Synchronizer
from crm_service.sync_jobs import consume_one
from tests.test_settings import basic_env
from tests.test_sync import FakeSource, data


def one(settings, **changes):
    return replace(settings, businesses={"tanaka": settings.businesses["tanaka"]}, **changes)


def test_page_budget_commits_checkpoints_without_partial_baseline(settings, sessions):
    settings = one(settings, max_pages=1, page_size=1)
    calls = []

    class Counted(FakeSource):
        def page(self, table, *, after, limit):
            calls.append((table, after))
            return super().page(table, after=after, limit=limit)

    worker = Synchronizer(settings, sessions, lambda _: Counted(data()))
    for _ in range(10):
        result = worker.run()["tanaka"]
        with sessions() as session:
            status = session.get(SyncStatus, "tanaka")
            assert status.metrics["pages"] <= 1
            assert status.metrics["requests"] <= settings.sync_max_requests
            assert status.baseline_complete == (result == "ok")
        if result == "ok":
            break
    else:
        pytest.fail("Checkpoint did not advance to baseline")
    assert calls.count(("message_logs", 0)) == 1
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(SourceMessage)) == 1
        assert session.scalar(select(func.count()).select_from(NotificationEvent)) == 0


def test_requests_counted_across_resources_not_per_table(settings, sessions):
    settings = one(settings, sync_max_requests=4, max_pages=100)
    worker = Synchronizer(settings, sessions, lambda _: FakeSource(data()))
    assert worker.run()["tanaka"] == "partial"
    with sessions() as session:
        assert session.get(SyncStatus, "tanaka").metrics["requests"] == 4
        assert not session.get(SyncStatus, "tanaka").baseline_complete
        assert session.get(SyncCursor, ("tanaka", "cycle")).value["phase"] == 2
    assert worker.run()["tanaka"] == "ok"


def test_slow_business_yields_to_the_other_businesses(settings, sessions):
    clock = [0.0]

    class Slow(FakeSource):
        def page(self, table, *, after, limit):
            clock[0] += 0.1
            return super().page(table, after=after, limit=limit)

    worker = Synchronizer(
        replace(settings, sync_max_seconds=0.05),
        sessions,
        lambda b: Slow(data()) if b.id == "memos" else FakeSource(data()),
        clock=lambda: clock[0],
    )
    assert worker.run() == {"tanaka": "ok", "memos": "partial", "velvet": "ok"}
    with sessions() as session:
        assert not session.get(SyncStatus, "memos").baseline_complete
        assert session.get(SyncStatus, "memos").metrics["requests"] == 1


def test_failure_backoff_does_not_repeat_reads(settings, sessions):
    calls = []

    def factory(business):
        calls.append(business.id)
        return FakeSource(data(), failure=True)

    worker = Synchronizer(one(settings), sessions, factory)
    assert worker.run() == {"tanaka": "failed"}
    assert worker.run() == {"tanaka": "backoff"}
    assert calls == ["tanaka"]
    with sessions() as session:
        status = session.get(SyncStatus, "tanaka")
        assert status.failures == 1 and status.next_retry_at > now()
        assert status.metrics["requests"] == 1


def test_unchanged_poll_does_not_reproject_history(settings, sessions):
    worker = Synchronizer(settings, sessions, lambda _: FakeSource(data()))
    worker.run()
    statements = []

    def capture(conn, cursor, statement, parameters, context, many):
        if "FROM source_messages" in statement and "source_messages.phone_number =" in statement:
            statements.append(statement)

    engine = sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        worker.run()
        worker.run()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert statements == []


def test_history_limit_does_not_silently_truncate_or_complete_baseline(settings, sessions):
    rows = data()
    rows["message_logs"].append({**rows["message_logs"][0], "id": 2, "event_key": "other"})
    worker = Synchronizer(one(settings, sync_max_history_messages=1), sessions, lambda _: FakeSource(rows))
    assert worker.run()["tanaka"] == "failed"
    with sessions() as session:
        status = session.get(SyncStatus, "tanaka")
        assert not status.baseline_complete
        assert status.last_error == "conversation_history_limit_requires_review"


def test_default_worker_rejects_remote_before_opening_source(settings, sessions, monkeypatch):
    business = Business("tanaka", "", "", readonly_database_url="postgresql://reader@real.example.com/data")
    worker = Synchronizer(replace(settings, businesses={"tanaka": business}), sessions)
    monkeypatch.setattr(
        "crm_service.source_postgres.PostgresSourceAdapter",
        lambda *a, **kw: pytest.fail("Remote source must never open"),
    )
    assert worker.run()["tanaka"] == "failed"
    with sessions() as session:
        assert (
            session.get(SyncStatus, "tanaka").last_error
            == "production_sources_blocked_requires_permission_review"
        )


def test_queue_consumed_only_by_worker(settings, sessions):
    with sessions.begin() as session:
        job = SyncRequest(business_id="tanaka", idempotency_key="synthetic-once")
        session.add(job)
        session.flush()
        job_id = job.id
    worker = Synchronizer(settings, sessions, lambda _: FakeSource(data()))
    assert consume_one(worker, sessions) == "tanaka"
    assert consume_one(worker, sessions) is None
    with sessions() as session:
        job = session.get(SyncRequest, job_id)
        assert job.status == "done" and job.result == {"tanaka": "ok"}


def test_removed_business_job_does_not_block_valid_businesses(settings, sessions):
    with sessions.begin() as session:
        job = SyncRequest(business_id="retired", idempotency_key="old-request")
        session.add(job)
        session.flush()
        job_id = job.id
    worker = Synchronizer(settings, sessions, lambda _: FakeSource(data()))
    assert consume_one(worker, sessions) == "retired"
    assert set(worker.run().values()) == {"ok"}
    with sessions() as session:
        assert session.get(SyncRequest, job_id).status == "failed"
        assert session.get(SyncRequest, job_id).result == {"retired": "business_removed"}


def test_reconciliation_checkpoints_each_successful_row(settings, sessions):
    rows = data()
    rows["message_logs"] += [{**rows["message_logs"][0], "id": i, "event_key": f"event-{i}"} for i in (2, 3)]
    settings = one(settings, page_size=3)
    assert Synchronizer(settings, sessions, lambda _: FakeSource(rows)).run()["tanaka"] == "ok"
    settings = replace(settings, sync_max_seconds=0.05)
    for row in rows["message_logs"]:
        row["content"] = "updated historical message"
    clock, visited = [0.0], []

    class SlowRows(Synchronizer):
        def upsert_message(self, session, business, item):
            super().upsert_message(session, business, item)
            visited.append(item["id"])
            clock[0] += 0.06

    worker = SlowRows(settings, sessions, lambda _: FakeSource(rows), clock=lambda: clock[0])
    for expected in (1, 2, 3):
        assert worker.run()["tanaka"] == "partial"
        with sessions() as session:
            assert session.get(SyncCursor, ("tanaka", "message_reconciliation")).value["id"] == expected
    assert worker.run()["tanaka"] == "ok"
    assert visited == [1, 2, 3]


@pytest.mark.parametrize(
    "second",
    [
        "postgresql://crm_staging_reader_two@localhost:55432/crm_staging_source_one",
        "postgresql://crm_staging_reader_one@127.0.0.1:55432/crm_staging_source_two",
    ],
)
def test_worker_rejects_duplicate_source_database_or_login(monkeypatch, second):
    basic_env(monkeypatch)
    monkeypatch.setenv("CRM_BUSINESSES", "one,two")
    monkeypatch.setenv(
        "ONE_SOURCE_READONLY_DATABASE_URL",
        "postgresql://crm_staging_reader_one@127.0.0.1:55432/crm_staging_source_one",
    )
    monkeypatch.setenv("TWO_SOURCE_READONLY_DATABASE_URL", second)
    with pytest.raises(ValueError, match="distinct source database"):
        Settings.from_env(service="worker")


@pytest.mark.parametrize(
    "changes",
    [
        {"max_pages": 0},
        {"sync_max_requests": 3},
        {"page_size": 1001},
        {"sync_max_seconds": float("nan")},
        {"sync_max_seconds": float("inf")},
        {"sync_max_seconds": 121},
        {"sync_max_connections": 0},
        {"sync_max_connections": 5},
        {"reconcile_interval": 0},
    ],
)
def test_invalid_budgets_fail_configuration(settings, changes):
    with pytest.raises(ValueError):
        replace(settings, **changes)


def test_api_does_not_load_worker_source_credentials(monkeypatch):
    basic_env(monkeypatch)
    monkeypatch.setenv("CRM_BUSINESSES", "future")
    monkeypatch.setenv("FUTURE_SOURCE_READONLY_DATABASE_URL", "malformed-on-purpose")
    settings = Settings.from_env(service="api")
    assert settings.businesses["future"].readonly_database_url == ""


def test_admin_source_keys_cannot_activate_worker(monkeypatch):
    basic_env(monkeypatch)
    monkeypatch.setenv("CRM_BUSINESSES", "future")
    monkeypatch.setenv("FUTURE_SOURCE_SUPABASE_SERVICE_ROLE_KEY", "synthetic-admin-key")
    with pytest.raises(ValueError, match="Administrative"):
        Settings.from_env(service="worker")


def test_notifications_cannot_be_activated_in_staging(monkeypatch):
    basic_env(monkeypatch)
    monkeypatch.setenv("NOTIFICATIONS_ENABLED", "true")
    with pytest.raises(ValueError, match="NOTIFICATIONS_ENABLED=false"):
        Settings.from_env()
