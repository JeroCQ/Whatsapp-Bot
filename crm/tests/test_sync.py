from copy import deepcopy
from dataclasses import replace

import pytest
from sqlalchemy import func, select, text

from crm_service.models import (
    CRMConversation,
    NotificationEvent,
    SourceConversation,
    SourceMessage,
    SyncCursor,
    SyncStatus,
)
from crm_service.source import SourceFailure
from crm_service.sync import Synchronizer, lock_id

BASE = "2026-11-01T12:00:00+00:00"


def data(paused=True, message_id=1):
    return {
        "customers": [{"phone_number": "573001", "name": "Cliente", "created_at": BASE}],
        "conversation_states": [
            {
                "phone_number": "573001",
                "is_paused": paused,
                "chatwoot_conversation_id": 123,
                "last_interaction": BASE,
            }
        ],
        "handoff_deliveries": [],
        "message_logs": [
            {
                "id": message_id,
                "phone_number": "573001",
                "role": "user",
                "content": "Hola",
                "created_at": BASE,
                "metadata": {},
                "event_key": f"event-{message_id}",
            }
        ],
    }


class FakeSource:
    def __init__(self, rows, failure=False):
        self.rows, self.failure = rows, failure
        self.warnings = set()

    def page(self, table, *, after, limit):
        if self.failure:
            raise SourceFailure("source_http_503")
        key = "id" if table == "message_logs" else "phone_number"
        return deepcopy(
            sorted(
                [row for row in self.rows[table] if after is None or row[key] > after],
                key=lambda row: row[key],
            )[:limit]
        )

    def close(self):
        pass


@pytest.fixture
def seeded(settings, sessions):
    datasets = {name: data(message_id=i) for i, name in enumerate(settings.businesses, 1)}
    worker = Synchronizer(replace(settings, page_size=1), sessions, lambda biz: FakeSource(datasets[biz.id]))
    assert set(worker.run().values()) == {"ok"}
    return datasets, worker


def test_repeated_sync_is_idempotent(seeded, sessions):
    _, worker = seeded
    assert worker.run() == {"tanaka": "ok", "memos": "ok", "velvet": "ok"}
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(SourceMessage)) == 3
        assert session.scalar(select(func.count()).select_from(SourceConversation)) == 3
        assert session.scalar(select(func.count()).select_from(CRMConversation)) == 3


def test_independent_business_cursors_and_same_phone(seeded, sessions):
    with sessions() as session:
        assert [
            session.get(SyncCursor, (b, "message_logs")).value["id"] for b in ("tanaka", "memos", "velvet")
        ] == [1, 2, 3]
        rows = session.scalars(select(SourceMessage)).all()
        assert len({r.business_id for r in rows}) == 3
        assert {r.phone_number for r in rows} == {"573001"}


def test_failed_business_does_not_block_others(settings, sessions):
    worker = Synchronizer(settings, sessions, lambda biz: FakeSource(data(), failure=biz.id == "memos"))
    assert worker.run() == {"tanaka": "ok", "memos": "failed", "velvet": "ok"}
    with sessions() as session:
        assert session.get(SyncStatus, "memos").last_error == "source_http_503"
        assert session.get(SyncStatus, "tanaka").baseline_complete
        assert session.get(SyncCursor, ("memos", "message_logs")) is None


def test_backfill_silent_then_new_transition_once(notifications_settings, sessions):
    datasets = {b: data() for b in notifications_settings.businesses}
    worker = Synchronizer(notifications_settings, sessions, lambda biz: FakeSource(datasets[biz.id]))
    worker.run()
    worker.run()
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(NotificationEvent)) == 0
    datasets["tanaka"]["conversation_states"][0]["is_paused"] = False
    worker.run()
    datasets["tanaka"]["conversation_states"][0]["is_paused"] = True
    worker.run()
    worker.run()
    with sessions() as session:
        events = session.scalars(select(NotificationEvent)).all()
        assert len(events) == 1 and events[0].business_id == "tanaka"
        assert "business_id=tanaka" in events[0].link and "bucket=awaiting_human" in events[0].link


def test_changed_old_message_is_reconciled(seeded, sessions):
    datasets, worker = seeded
    datasets["tanaka"]["message_logs"][0]["content"] = "transcripción actualizada"
    for _ in range(3):
        worker.run()
    with sessions() as session:
        assert session.get(SourceMessage, ("tanaka", 1)).content == "transcripción actualizada"


def test_business_mismatch_rolls_back(settings, sessions):
    rows = data()
    rows["handoff_deliveries"] = [{"business_id": "other", "phone_number": "573001"}]
    worker = Synchronizer(settings, sessions, lambda biz: FakeSource(rows))
    assert set(worker.run().values()) == {"failed"}
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(SourceConversation)) == 0


def test_lock_prevents_duplicate_sync(settings, sessions):
    worker = Synchronizer(settings, sessions, lambda biz: FakeSource(data()))
    with sessions.begin() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id("tanaka")})
        result = worker.run()
        assert result["tanaka"] == "already_running"
        assert result["memos"] == "ok"


def test_cursor_transaction_rollback_on_partial_page_failure(settings, sessions):
    class Broken(FakeSource):
        def page(self, table, *, after, limit):
            if table == "message_logs" and after:
                raise SourceFailure("source_http_503")
            return super().page(table, after=after, limit=limit)

    worker = Synchronizer(settings, sessions, lambda biz: Broken(data()))
    assert set(worker.run().values()) == {"failed"}
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(SourceMessage)) == 0
        assert session.scalar(select(func.count()).select_from(SyncCursor)) == 0


def test_crm_fields_survive_sync(seeded, sessions):
    _, worker = seeded
    with sessions.begin() as session:
        row = session.get(CRMConversation, ("tanaka", "573001"))
        row.stage, row.tags = "qualified", ["interesado"]
    worker.run()
    with sessions() as session:
        row = session.get(CRMConversation, ("tanaka", "573001"))
        assert row.stage == "qualified" and row.tags == ["interesado"]


def test_data_quality_warning_is_per_business(settings, sessions):
    def factory(biz):
        adapter = FakeSource(data())
        if biz.id == "memos":
            adapter.warnings.add("missing_column:message_logs.metadata")
        return adapter

    Synchronizer(settings, sessions, factory).run()
    with sessions() as session:
        assert session.get(SyncStatus, "memos").warnings == ["missing_column:message_logs.metadata"]
        assert not session.get(SyncStatus, "tanaka").warnings


def test_duplicate_event_key_is_deduplicated(settings, sessions):
    rows = data()
    duplicate = {**rows["message_logs"][0], "id": 9}
    rows["message_logs"].append(duplicate)
    worker = Synchronizer(settings, sessions, lambda biz: FakeSource(rows))
    assert set(worker.run().values()) == {"ok"}
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(SourceMessage)) == 3


def test_second_attention_transition_gets_new_event(notifications_settings, sessions):
    rows = data(paused=False)
    worker = Synchronizer(notifications_settings, sessions, lambda biz: FakeSource(rows))
    worker.run()
    for paused in (True, False, True):
        rows["conversation_states"][0]["is_paused"] = paused
        worker.run()
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(NotificationEvent)) == 6


def test_existing_message_cannot_move_between_phones(seeded, sessions):
    datasets, worker = seeded
    datasets["tanaka"]["message_logs"][0]["phone_number"] = "different-phone"
    assert worker.run()["tanaka"] == "failed"
    assert worker.run()["tanaka"] == "backoff"
    with sessions() as session:
        assert session.get(SourceMessage, ("tanaka", 1)).phone_number == "573001"
        assert session.get(SourceConversation, ("tanaka", "different-phone")) is None
