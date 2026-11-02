from datetime import timedelta

import pytest

from crm_service.models import Agent, NotificationEvent, PushDelivery, PushSubscription, now
from crm_service.notifications import dispatch, endpoint_hash
from crm_service.sync import Synchronizer
from tests.test_sync import FakeSource, data


def seed(settings, sessions, *, expired=False, preferences=None):
    Synchronizer(settings, sessions, lambda biz: FakeSource(data())).run()
    with sessions.begin() as session:
        agent = Agent(profile="agent", allowed_businesses=["tanaka"], preferences=preferences or {})
        session.add(agent)
        session.flush()
        endpoint = "https://fcm.googleapis.com/fcm/send/synthetic"
        sub = PushSubscription(
            profile="agent",
            endpoint_hash=endpoint_hash(endpoint),
            subscription={"endpoint": endpoint, "keys": {"auth": "synthetic", "p256dh": "synthetic"}},
            created_at=now() - timedelta(minutes=1),
        )
        session.add(sub)
        session.flush()
        event = NotificationEvent(
            business_id="tanaka",
            phone_number="573001",
            transition=10,
            link="/chats?business_id=tanaka",
            created_at=now(),
        )
        session.add(event)
        session.flush()
        return sub.id, event.id


def test_push_deduplicated_per_subscription_and_transition(notifications_settings, sessions):
    sub_id, event_id = seed(notifications_settings, sessions)
    calls = []

    def sender(sub, payload, config):
        calls.append(payload)
        return 201

    assert dispatch(notifications_settings, sessions, sender) == 1
    assert dispatch(notifications_settings, sessions, sender) == 0
    assert len(calls) == 1 and calls[0]["tag"] == event_id
    with sessions() as session:
        assert session.get(PushDelivery, (event_id, sub_id)).status == "sent"


@pytest.mark.parametrize("status", [404, 410])
def test_expired_subscription_is_disabled(notifications_settings, sessions, status):
    sub_id, _ = seed(notifications_settings, sessions)
    dispatch(notifications_settings, sessions, lambda *_: status)
    with sessions() as session:
        assert session.get(PushSubscription, sub_id).active is False
    assert dispatch(notifications_settings, sessions, lambda *_: pytest.fail("must not retry")) == 0


def test_push_preferences_and_tenant_scope(notifications_settings, sessions):
    seed(notifications_settings, sessions, preferences={"tanaka": {"web_push": False}})
    with sessions.begin() as session:
        session.add(
            NotificationEvent(business_id="memos", phone_number="573001", transition=10, link="/chats")
        )
    assert dispatch(notifications_settings, sessions, lambda *_: pytest.fail("must not send")) == 0


def test_disabled_by_default(settings, sessions):
    assert not settings.notifications_enabled
    assert dispatch(settings, sessions, lambda *_: pytest.fail("must not send")) == 0


def test_new_subscription_does_not_replay_old_events(notifications_settings, sessions):
    sub_id, _ = seed(notifications_settings, sessions)
    with sessions.begin() as session:
        session.get(PushSubscription, sub_id).created_at = now() + timedelta(minutes=1)
    assert dispatch(notifications_settings, sessions, lambda *_: pytest.fail("must not replay")) == 0


def test_failed_push_is_deferred(notifications_settings, sessions):
    sub_id, event_id = seed(notifications_settings, sessions)
    calls = []
    dispatch(notifications_settings, sessions, lambda *_: calls.append(1) or 503)
    dispatch(notifications_settings, sessions, lambda *_: calls.append(1) or 503)
    assert len(calls) == 1
    with sessions() as session:
        row = session.get(PushDelivery, (event_id, sub_id))
        assert row.status == "pending" and row.attempts == 1 and row.retry_at > now()


def test_revoked_access_disables_pending_delivery(notifications_settings, sessions):
    sub_id, event_id = seed(notifications_settings, sessions)
    dispatch(notifications_settings, sessions, lambda *_: 503)
    with sessions.begin() as session:
        session.get(Agent, "agent").allowed_businesses = []
        session.get(PushDelivery, (event_id, sub_id)).retry_at = now()
    dispatch(notifications_settings, sessions, lambda *_: pytest.fail("revoked scope"))
    with sessions() as session:
        assert session.get(PushDelivery, (event_id, sub_id)).status == "disabled"


def test_enabling_preference_does_not_replay_suppressed_push(notifications_settings, sessions):
    seed(notifications_settings, sessions, preferences={"tanaka": {"web_push": False}})
    dispatch(notifications_settings, sessions, lambda *_: pytest.fail("preference disabled"))
    with sessions.begin() as session:
        session.get(Agent, "agent").preferences = {"tanaka": {"web_push": True}}
    assert dispatch(notifications_settings, sessions, lambda *_: pytest.fail("suppressed event replay")) == 0
