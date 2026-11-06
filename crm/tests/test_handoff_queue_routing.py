"""Synthetic reproduction of a B2B handoff arriving in Memo's retail queue."""
from datetime import timedelta

from sqlalchemy import select

from crm_service.models import (
    Agent,
    CRMConversation,
    NotificationEvent,
    PushSubscription,
    SourceConversation,
    now,
)
from crm_service.notifications import dispatch
from crm_service.routing import customer_queue, project_queue
from crm_service.sync import Synchronizer
from tests.test_sync import FakeSource, data

PHONE = "573001"
WAITING = {
    "is_paused": True, "crm_control_phase": "waiting", "crm_control_owner": None,
    "crm_control_version": 7, "handoff_reason": "B2B_HIGH_VALUE: interés en venta al por mayor",
    "customer_data": {"tipo_cliente": "Detal"},
}


def test_current_b2b_handoff_overrides_missing_or_stale_type_not_an_old_resolved_reason():
    assert customer_queue(WAITING) == ("wholesale", False)
    assert customer_queue({**WAITING, "customer_data": {}}) == ("wholesale", False)
    assert customer_queue({**WAITING, "is_paused": False}) == ("retail", False)
    assert customer_queue({"customer_data": {}}) == ("retail", True)


def test_unowned_handoff_repairs_old_queue_but_preserves_manual_transfer_and_newer_owner():
    old = {"attention_queue": "retail", "queue_needs_classification": True}
    routed = project_queue(WAITING, old, False)
    assert routed["attention_queue"] == "wholesale" and not routed["queue_needs_classification"]
    assert project_queue(WAITING, routed, False) == routed
    legacy_transfer = {**old, "queue_transferred": True}
    saved = project_queue(WAITING, legacy_transfer, False)
    assert saved["attention_queue"] == "retail" and saved["queue_transferred"]
    assert project_queue(WAITING, saved, False) == saved
    fresh = project_queue({**WAITING, "crm_control_version": 8}, saved, False)
    assert fresh["attention_queue"] == "wholesale" and not fresh["queue_transferred"]
    owned = {**old, "queue_control_version": 8}
    assert project_queue(WAITING, owned, False)["attention_queue"] == "retail"
    human = {**WAITING, "crm_control_phase": "human", "crm_control_owner": "operator:memos", "crm_control_version": 8}
    assert project_queue(human, old, False)["attention_queue"] == "retail"


def test_new_wholesale_handoff_and_push_go_only_to_wholesale(notifications_settings, sessions):
    rows = data(paused=False)
    worker = Synchronizer(notifications_settings, sessions, lambda _: FakeSource(rows))
    assert worker.run("memos") == {"memos": "ok"}
    with sessions.begin() as session:
        for queue in ("retail", "wholesale"):
            profile = "operator:" + queue
            session.add(Agent(profile=profile, allowed_businesses=["memos"], preferences={
                "_crm_operator": {"role": "operator", "queue": queue, "since": (now() - timedelta(hours=1)).isoformat()},
            }))
            session.add(PushSubscription(profile=profile, endpoint_hash=queue,
                subscription={"endpoint": "https://fcm.googleapis.com/synthetic/" + queue, "keys": {}}))
    rows["conversation_states"][0].update(WAITING)
    rows["message_logs"].append({"id": 2, "phone_number": PHONE, "role": "system",
        "content": "HANDOFF: " + WAITING["handoff_reason"], "created_at": now().isoformat(),
        "metadata": {}, "event_key": "synthetic-b2b-handoff"})
    assert worker.run("memos") == {"memos": "ok"}
    assert worker.run("memos") == {"memos": "ok"}
    with sessions() as session:
        crm = session.get(CRMConversation, ("memos", PHONE))
        assert crm.classification["attention_queue"] == "wholesale"
        assert crm.assigned_to == "operator:wholesale"
        assert len(session.scalars(select(NotificationEvent)).all()) == 1
    recipients = []
    assert dispatch(notifications_settings, sessions, lambda sub, payload, _: recipients.append(payload["profile"]) or 201) == 1
    assert recipients == ["operator:wholesale"]


def test_existing_bad_projection_repairs_without_dirty_source_or_historical_push(notifications_settings, sessions):
    rows = data()
    rows["conversation_states"][0].update(WAITING)
    worker = Synchronizer(notifications_settings, sessions, lambda _: FakeSource(rows))
    assert worker.run("memos") == {"memos": "ok"}
    with sessions.begin() as session:
        crm = session.get(CRMConversation, ("memos", PHONE))
        crm.classification = {k: v for k, v in crm.classification.items() if not k.startswith("queue_")}
        crm.classification = {**crm.classification, "attention_queue": "retail"}
        crm.assigned_to = "operator:retail"
        session.get(SourceConversation, ("memos", PHONE)).dirty = False
    assert worker.run("memos") == {"memos": "ok"}
    with sessions() as session:
        crm = session.get(CRMConversation, ("memos", PHONE))
        assert crm.classification["attention_queue"] == "wholesale" and crm.assigned_to is None
        assert session.scalars(select(NotificationEvent)).all() == []
