from datetime import datetime, timedelta, timezone
from dataclasses import replace
from types import SimpleNamespace

from sqlalchemy import func, select

from crm_service.bot_commands import CommandCoordinator
from crm_service.customer_analysis import consume_analysis, latest_analysis
from crm_service.models import BotCommand, CRMConversation, CustomerAnalysis, PromotionPermission, PromotionPermissionEvent, SourceConversation
from crm_service.promotion_permissions import QUESTION, VERSION
from crm_service.sync import Synchronizer

PHONE = "573000000001"
SENDER = "123456789"
AT = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)


def message(source_id, role, content, metadata, at=AT):
    return {"id": source_id, "phone_number": PHONE, "role": role, "content": content,
            "metadata": metadata, "event_key": f"synthetic:{source_id}", "created_at": at.isoformat()}


def reply(source_id, answer, *, question="wamid.question", minutes=0):
    at = AT + timedelta(minutes=minutes)
    return message(source_id, "system", answer, {
        "source": "promotion_permission", "channel_id": SENDER,
        "source_event_id": f"wamid.answer.{source_id}", "permission_question_id": question,
        "permission_status": answer, "permission_version": VERSION, "received_at": at.isoformat(),
    }, at)


def test_permission_matches_its_question_across_delayed_sync_and_isolated_businesses(settings, sessions):
    sync = Synchronizer(settings, sessions)
    outbound = message(1, "asesor", QUESTION, {"source": "human", "delivery_status": "accepted",
                                              "provider_message_id": "wamid.question"}, AT - timedelta(minutes=1))
    with sessions.begin() as session:
        sync.upsert_message(session, "tanaka", reply(2, "granted"))
        sync.upsert_message(session, "velvet", outbound)
        assert session.get(PromotionPermission, ("tanaka", SENDER, PHONE)) is None
        sync.upsert_message(session, "tanaka", outbound)
        sync.upsert_message(session, "tanaka", reply(2, "granted"))
    with sessions.begin() as session:
        assert session.get(PromotionPermission, ("tanaka", SENDER, PHONE)).status == "granted"
        assert session.get(PromotionPermission, ("velvet", SENDER, PHONE)) is None
        assert session.scalar(select(func.count()).select_from(PromotionPermissionEvent)) == 1
        sync.upsert_message(session, "tanaka", reply(3, "revoked", minutes=4, question=None))
        sync.upsert_message(session, "tanaka", reply(4, "granted", minutes=5))
    with sessions.begin() as session:
        assert session.get(PromotionPermission, ("tanaka", SENDER, PHONE)).status == "revoked"
        sync.upsert_message(session, "tanaka", reply(5, "granted", minutes=6, question=None))
    with sessions.begin() as session:
        assert session.get(PromotionPermission, ("tanaka", SENDER, PHONE)).status == "granted"


def test_no_is_distinct_from_no_response(settings, sessions):
    sync = Synchronizer(settings, sessions)
    with sessions.begin() as session:
        sync.upsert_message(session, "memos", message(1, "asesor", QUESTION, {
            "source": "human", "delivery_status": "accepted", "provider_message_id": "wamid.question",
        }, AT - timedelta(minutes=1)))
        assert session.get(PromotionPermission, ("memos", SENDER, PHONE)) is None
        sync.upsert_message(session, "memos", reply(2, "denied"))
    with sessions.begin() as session:
        assert session.get(PromotionPermission, ("memos", SENDER, PHONE)).status == "denied"


def test_only_a_confirmed_return_enqueues_one_job(sessions):
    with sessions.begin() as session:
        session.add(SourceConversation(business_id="tanaka", phone_number=PHONE))
        session.flush()
        session.add(CRMConversation(business_id="tanaka", phone_number=PHONE, bucket="awaiting_human", sort_key=""))
        session.add(BotCommand(business_id="tanaka", command_id="return-1", phone_number=PHONE,
                               profile="admin", operation="close", request_hash="synthetic",
                               status="forwarding", http_status=202, response={}))
    gateway = SimpleNamespace(business=SimpleNamespace(id="tanaka"))
    coordinator = CommandCoordinator(sessions, gateway, analyze_return=True)
    coordinator._save("return-1", {"command_id": "return-1", "status": "unknown"}, 202)
    with sessions() as session:
        assert session.get(CustomerAnalysis, ("tanaka", "return-1")) is None
    result = {"command_id": "return-1", "status": "confirmed",
              "control": {"phase": "ai", "is_paused": False, "version": 2}}
    coordinator._save("return-1", result, 200)
    coordinator._save("return-1", result, 200, recovery=True)
    with sessions() as session:
        job = session.get(CustomerAnalysis, ("tanaka", "return-1"))
        assert job is not None and job.status == "pending"
        assert session.scalar(select(func.count()).select_from(CustomerAnalysis)) == 1


def test_worker_persists_the_report_once_for_the_original_business(settings, sessions, monkeypatch):
    with sessions.begin() as session:
        session.add(CustomerAnalysis(business_id="tanaka", command_id="return-2", phone_number=PHONE, released_at=AT))
    calls = []

    class FakeSourceAdapter:
        def __init__(self, business, **_options):
            assert business.id == "tanaka"

        def return_analysis(self, phone, cutoff):
            calls.append((phone, cutoff))
            return {"summary": "Consulta resuelta", "labels": [], "confirmed_order_count": None}

        def close(self):
            pass

    monkeypatch.setattr("crm_service.customer_analysis.BotSourceAdapter", FakeSourceAdapter)
    enabled = replace(settings, customer_insights_businesses=("tanaka",))
    consume_analysis(enabled, sessions, "memos")
    consume_analysis(enabled, sessions)
    consume_analysis(enabled, sessions)
    assert calls == [(PHONE, AT)]
    with sessions() as session:
        report = latest_analysis(session, "tanaka", PHONE)
        assert report["status"] == "done" and report["summary"] == "Consulta resuelta"
        assert report["confirmed_order_count"] is None
        assert latest_analysis(session, "memos", PHONE) is None
