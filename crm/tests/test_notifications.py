import base64
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from crm_service.models import Agent, NotificationEvent, PushDelivery, PushSubscription, now
from crm_service.notifications import dispatch, endpoint_hash, push_config, send_push, vapid_identity
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
    assert calls[0]["profile"] == "agent" and calls[0]["subscription_id"] == sub_id
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


def test_push_configuration_exposes_only_derived_public_key(notifications_settings):
    config = push_config(notifications_settings)
    assert config["configured"] and config["enabled"] and config["reason"] is None
    public = base64.urlsafe_b64decode(config["public_key"] + "=")
    assert len(public) == 65 and public[0] == 4
    assert notifications_settings.vapid_private_key not in str(config)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"notifications_enabled": False}, "disabled"),
        ({"vapid_private_key": ""}, "vapid_missing"),
        ({"vapid_private_key": "not-a-private-key"}, "vapid_invalid"),
        ({"vapid_subject": ""}, "vapid_subject_missing"),
        ({"vapid_subject": "https://[malformed"}, "vapid_subject_missing"),
        ({"vapid_subject": "mailto:missing-address"}, "vapid_subject_missing"),
    ],
)
def test_push_configuration_reports_missing_configuration_without_claiming_success(
    notifications_settings, changes, reason
):
    config = push_config(replace(notifications_settings, **changes))
    assert not config["configured"] and config["public_key"] is None
    assert config["reason"] == reason


def test_vapid_pem_is_supported_but_non_p256_keys_are_rejected(notifications_settings):
    identity = vapid_identity(notifications_settings.vapid_private_key)
    pem = identity.private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    assert push_config(replace(notifications_settings, vapid_private_key=pem)) == push_config(
        notifications_settings
    )
    other_curve = ec.generate_private_key(ec.SECP384R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    assert push_config(replace(notifications_settings, vapid_private_key=other_curve))["reason"] == "vapid_invalid"


def test_sender_uses_the_same_validated_vapid_identity(notifications_settings, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "crm_service.notifications.webpush",
        lambda **kw: calls.append(kw) or SimpleNamespace(status_code=201),
    )
    subscription = {"endpoint": "https://web.push.apple.com/synthetic", "keys": {}}
    assert send_push(subscription, {"tag": "synthetic-event"}, notifications_settings) == 201
    assert calls[0]["vapid_private_key"] is vapid_identity(notifications_settings.vapid_private_key)
    assert calls[0]["vapid_claims"] == {"sub": notifications_settings.vapid_subject}


def test_missing_vapid_never_contacts_push_provider(notifications_settings, sessions):
    seed(notifications_settings, sessions)
    unavailable = replace(notifications_settings, vapid_private_key="")
    assert dispatch(unavailable, sessions, lambda *_: pytest.fail("must not send")) == 0
