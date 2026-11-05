from copy import deepcopy
from dataclasses import replace

from sqlalchemy import func, select

from crm_service.models import CRMConversation, SourceConversation, SourceMessage, SyncCursor, SyncStatus
from crm_service.source import SourceFailure
from crm_service.sync import Synchronizer
from tests.test_sync import FakeSource, data


class TargetedSource(FakeSource):
    def __init__(self, rows, calls):
        super().__init__(rows)
        self.calls = calls

    def page(self, table, *, after, limit):
        self.calls.append((table, "page"))
        return super().page(table, after=after, limit=limit)

    def conversation(self, table, phone):
        self.calls.append((table, phone))
        return deepcopy([row for row in self.rows[table] if row["phone_number"] == phone])


def prepare(settings, sessions):
    rows, calls = data(), []
    config = replace(settings, businesses={"tanaka": settings.businesses["tanaka"]})
    worker = Synchronizer(config, sessions, lambda _: TargetedSource(rows, calls))
    assert worker.run() == {"tanaka": "ok"}
    worker.settings = replace(config, source_mode="bot_api")
    extra = data(message_id=2)
    for table, values in extra.items():
        for value in values:
            value["phone_number"] = "573002"
            if table == "customers":
                value["name"] = "Nombre real de Meta"
            if table == "conversation_states":
                value.update(human_support_provider="crm", crm_control_phase="waiting")
            rows[table].append(value)
    calls.clear()
    return worker, calls


def test_live_messages_and_profile_commit_without_rescanning_all_contacts(settings, sessions):
    worker, calls = prepare(settings, sessions)
    assert worker.run() == {"tanaka": "ok"}
    assert calls[:2] == [("message_logs", "page"), ("message_logs", "page")]
    assert all(table == "message_logs" or phone == "573002" for table, phone in calls)
    with sessions() as session:
        source = session.get(SourceConversation, ("tanaka", "573002"))
        assert source.name == "Nombre real de Meta"
        assert source.state["is_paused"] is True
        assert session.get(CRMConversation, ("tanaka", "573002")).bucket == "awaiting_human"
    assert worker.run() == {"tanaka": "ok"}
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(SourceMessage)) == 2


def test_live_pass_resumes_one_page_at_a_time_without_repeating_contact_stage(settings, sessions):
    worker, calls = prepare(settings, sessions)
    worker.settings = replace(worker.settings, max_pages=1)
    for _ in range(8):
        result = worker.run()["tanaka"]
        with sessions() as session:
            status = session.get(SyncStatus, "tanaka")
            assert status.baseline_complete
            assert status.metrics["pages"] <= 1
        if result == "ok":
            break
    else:
        raise AssertionError("Live checkpoint did not advance")
    assert calls.count(("customers", "573002")) == 1
    assert calls.count(("conversation_states", "573002")) == 1
    with sessions() as session:
        assert session.get(CRMConversation, ("tanaka", "573002")).bucket == "awaiting_human"


def test_live_source_cannot_replace_one_contacts_state_with_another(settings, sessions):
    worker, _ = prepare(settings, sessions)

    class CrossPhone(TargetedSource):
        def conversation(self, table, phone):
            return [{"phone_number": "another-contact", "is_paused": False}]

    rows = worker.factory(worker.settings.businesses["tanaka"]).rows
    worker.factory = lambda _: CrossPhone(rows, [])
    assert worker.run() == {"tanaka": "failed"}
    with sessions() as session:
        assert session.get(SourceConversation, ("tanaka", "573002")) is None


def test_slow_historical_reconciliation_does_not_roll_back_live_messages_or_back_them_off(settings, sessions):
    worker, calls = prepare(settings, sessions)
    rows = worker.factory(worker.settings.businesses["tanaka"]).rows

    class FailedSweep(TargetedSource):
        def page(self, table, *, after, limit):
            if table == "customers":
                raise SourceFailure("synthetic_historical_unavailable")
            return super().page(table, after=after, limit=limit)

    worker.factory = lambda _: FailedSweep(rows, calls)
    with sessions.begin() as session:
        session.get(SyncCursor, ("tanaka", "mutable_refresh")).value = {}
    assert worker.run() == {"tanaka": "partial"}
    with sessions() as session:
        assert session.get(CRMConversation, ("tanaka", "573002")).bucket == "awaiting_human"
        assert session.get(SyncStatus, "tanaka").next_retry_at is None
    assert worker.run() == {"tanaka": "ok"}
