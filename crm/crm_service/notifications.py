import hashlib
import json
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from crm_service.models import Agent, NotificationEvent, PushDelivery, PushSubscription, now
from crm_service.settings import Settings


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
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},
            ttl=300,
            timeout=10,
        )
        return int(response.status_code)
    except WebPushException as error:
        return int(error.response.status_code) if error.response is not None else 503


def dispatch(settings: Settings, sessions: sessionmaker[Session], sender: Any = send_push) -> int:
    if not settings.notifications_enabled or not settings.vapid_private_key:
        return 0
    # Serialize creation of outbox recipients across workers; per-delivery SKIP LOCKED below.
    with sessions.begin() as session:
        from sqlalchemy import text

        if not session.scalar(text("SELECT pg_try_advisory_xact_lock(724190031)")):
            return 0
        for pending_event in session.scalars(select(NotificationEvent)):
            for recipient in session.scalars(
                select(PushSubscription).where(
                    PushSubscription.active.is_(True), PushSubscription.created_at <= pending_event.created_at
                )
            ):
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
