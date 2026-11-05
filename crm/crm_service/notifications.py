import base64
import hashlib
import json
import time
from datetime import timedelta
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid
from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from crm_service.models import Agent, NotificationEvent, PushDelivery, PushSubscription, now
from crm_service.settings import Settings


@lru_cache(maxsize=4)
def vapid_identity(private_key: str) -> Any:
    # Accept actual key material, never a client-supplied path or a filename.
    identity = (
        Vapid.from_pem(private_key.encode())
        if private_key.startswith("-----BEGIN ")
        else Vapid.from_string(private_key)
    )
    if not isinstance(identity.private_key.curve, ec.SECP256R1):
        raise ValueError("VAPID requires a P-256 key")
    return identity


def push_config(settings: Settings) -> dict[str, Any]:
    reason = None
    public = None
    if not settings.notifications_enabled:
        reason = "disabled"
    elif not settings.vapid_private_key:
        reason = "vapid_missing"
    else:
        try:
            identity = vapid_identity(settings.vapid_private_key)
            public = base64.urlsafe_b64encode(
                identity.public_key.public_bytes(
                    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
                )
            ).decode().rstrip("=")
        except Exception:
            reason = "vapid_invalid"
        try:
            subject = urlsplit(settings.vapid_subject)
            valid_subject = bool(
                (subject.scheme == "mailto" and "@" in subject.path)
                or (
                    subject.scheme == "https"
                    and subject.hostname
                    and not subject.username
                    and not subject.password
                )
            )
        except ValueError:
            valid_subject = False
        if reason is None and not valid_subject:
            reason = "vapid_subject_missing"
    return {
        "enabled": settings.notifications_enabled,
        "configured": reason is None,
        "public_key": public if reason is None else None,
        "reason": reason,
    }


def endpoint_allowed(endpoint: str, settings: Settings) -> bool:
    url = urlsplit(endpoint)
    return (
        url.scheme == "https"
        and not url.username
        and not url.password
        and not url.fragment
        and url.port in (None, 443)
        and any(
            url.hostname == host or (url.hostname or "").endswith("." + host) for host in settings.push_hosts
        )
    )


def endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()


def preference(agent: Agent, business: str, channel: str) -> bool:
    return business in agent.allowed_businesses and bool(
        agent.preferences.get(business, {}).get(channel, True)
    )


def send_push(subscription: dict[str, Any], payload: dict[str, Any], settings: Settings) -> int:
    if not endpoint_allowed(subscription["endpoint"], settings):
        return 410
    try:
        response = webpush(
            subscription_info=subscription,
            data=json.dumps(payload),
            vapid_private_key=vapid_identity(settings.vapid_private_key),
            vapid_claims={"sub": settings.vapid_subject},
            ttl=300,
            timeout=10,
        )
        return int(response.status_code)
    except WebPushException as error:
        return int(error.response.status_code) if error.response is not None else 503


def dispatch(settings: Settings, sessions: sessionmaker[Session], sender: Any = send_push) -> int:
    if not push_config(settings)["configured"]:
        return 0
    started = time.monotonic()
    # Serialize creation of outbox recipients across workers; per-delivery SKIP LOCKED below.
    with sessions.begin() as session:
        from sqlalchemy import text

        if not session.scalar(text("SELECT pg_try_advisory_xact_lock(724190031)")):
            return 0
        # Seed only missing pairs instead of traversing the whole notification
        # history on every polling cycle. An expired/disabled recipient cannot
        # acquire a new pending delivery after a later preference change.
        missing_pairs = session.execute(
            select(NotificationEvent, PushSubscription)
            .join(PushSubscription, PushSubscription.created_at <= NotificationEvent.created_at)
            .outerjoin(
                PushDelivery,
                (PushDelivery.event_id == NotificationEvent.id)
                & (PushDelivery.subscription_id == PushSubscription.id),
            )
            .where(PushSubscription.active.is_(True), PushDelivery.event_id.is_(None))
            .order_by(NotificationEvent.created_at, NotificationEvent.id, PushSubscription.id)
            .limit(100)
        )
        for pending_event, recipient in missing_pairs:
            owner = session.get(Agent, recipient.profile)
            if session.get(PushDelivery, (pending_event.id, recipient.id)) is None:
                allowed = owner and preference(owner, pending_event.business_id, "web_push")
                session.add(
                    PushDelivery(
                        event_id=pending_event.id,
                        subscription_id=recipient.id,
                        status="pending" if allowed else "disabled",
                    )
                )
                session.flush()
    count = 0
    for _ in range(100):
        if time.monotonic() - started >= 5:
            break
        with sessions.begin() as session:
            delivery = session.scalar(
                select(PushDelivery)
                .where(PushDelivery.status == "pending", PushDelivery.retry_at <= now())
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if delivery is None:
                break
            event = session.get(NotificationEvent, delivery.event_id)
            sub = session.get(PushSubscription, delivery.subscription_id)
            agent = session.get(Agent, sub.profile) if sub else None
            if (
                not event
                or not sub
                or not sub.active
                or sub.created_at > event.created_at
                or not agent
                or not preference(agent, event.business_id, "web_push")
            ):
                delivery.status = "disabled"
                continue
            delivery.attempts += 1
            try:
                status = sender(
                    sub.subscription,
                    {
                        "id": event.id,
                        "tag": event.id,
                        "title": "Conversación por responder",
                        "url": event.link,
                        "profile": sub.profile,
                        "subscription_id": sub.id,
                    },
                    settings,
                )
            except Exception:
                # Never print endpoint URLs, subscription keys or provider response bodies.
                status = 503
            if status in (404, 410):
                sub.active, delivery.status = False, "expired"
            elif 200 <= status < 300:
                delivery.status = "sent"
                count += 1
            elif delivery.attempts >= 5:
                delivery.status = "failed"
            else:
                delivery.retry_at = now() + timedelta(seconds=30 * 2**delivery.attempts)
    return count
