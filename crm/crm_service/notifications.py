import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid
from pywebpush import WebPushException, webpush
from sqlalchemy import DateTime, cast, select
from sqlalchemy.orm import Session, sessionmaker

from crm_service.classification import timestamp
from crm_service.models import Agent, CRMConversation, NotificationEvent, PushDelivery, PushSubscription, ReadState, SourceConversation, now
from crm_service.routing import attention_lock, attention_queue
from crm_service.settings import Settings

logger = logging.getLogger(__name__)
NOTICE_MAX_AGE = timedelta(hours=24)


class PushResult(int):
    def __new__(cls, status: int, retry_after: str = ""):
        result = super().__new__(cls, status)
        result.retry_delay = 0
        try:
            result.retry_delay = min(3600, max(0, int(retry_after)))
        except (TypeError, ValueError):
            try:
                at = parsedate_to_datetime(retry_after)
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
                result.retry_delay = min(3600, max(0, int((at - now()).total_seconds())))
            except (TypeError, ValueError, OverflowError):
                pass
        return result


def compact_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def notice_snapshot(source: SourceConversation, messages: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    incoming = max((m for m in messages if m.get("role") == "user"),
                   key=lambda m: int(m["id"]), default={})
    raw = compact_text(source.state.get("handoff_reason") or source.handoff.get("reason"), 220)
    code, _, detail = raw.partition(":")
    reasons = {"B2B_HIGH_VALUE": "Solicitud de compra al por mayor", "USER_REQUEST": "El cliente pidió un asesor",
               "HUMAN_REQUEST": "El cliente pidió un asesor", "UNSUPPORTED_MEDIA": "Archivo que requiere revisión humana"}
    reason = reasons.get(code, raw) or "Motivo no informado por el bot"
    if code in reasons and detail.strip():
        reason = compact_text(reason + ": " + detail, 220)
    preview = compact_text(incoming.get("content"), 180)
    if not preview or preview.startswith(("http://", "https://", "data:")):
        preview = "Mensaje o archivo del cliente"
    return {"kind": kind, "reason": reason, "preview": preview,
            "message_id": int(incoming.get("id", 0))}


def notice_content(session: Session, event: NotificationEvent, crm: CRMConversation | None = None,
                   source: SourceConversation | None = None) -> dict[str, Any]:
    crm = crm or session.get(CRMConversation, (event.business_id, event.phone_number))
    source = source or session.get(SourceConversation, (event.business_id, event.phone_number))
    snapshot = crm.classification.get("notification", {}) if crm else {}
    kind = snapshot.get("kind", "handoff")
    brand = {"memos": "Memo’s", "briosos": "Briosos", "tanaka": "Tanaka", "velvet": "Velvet"}.get(event.business_id, event.business_id)
    name = compact_text(source.name if source else "", 60) or event.phone_number
    title = ("Nuevo caso · " if kind == "handoff" else "Caso transferido · " if kind == "transfer" else "") + brand + " · " + name
    reason = compact_text(snapshot.get("reason") or (source.state.get("handoff_reason") if source else ""), 220)
    reason = reason or "Motivo no informado por el bot"
    preview = compact_text(snapshot.get("preview"), 180) or "El cliente necesita atención humana."
    return {"title": title[:120], "body": f"Motivo: {reason}\n{preview}", "kind": kind,
            "reason": reason, "preview": preview, "business_id": event.business_id,
            "phone_number": event.phone_number, "message_id": int(snapshot.get("message_id", 0)),
            "sequence": event.transition, "created_at": event.created_at.isoformat(), "url": event.link}


def receipt_mac(settings: Settings, event_id: str, subscription_id: str, expires: int) -> str:
    # The existing shared VAPID identity also signs narrowly scoped receipts;
    # no general CRM credential is sent to a service worker or added to Railway.
    key = vapid_identity(settings.vapid_private_key).private_key.private_numbers().private_value.to_bytes(32, "big")
    return hmac.new(key, f"chatty-receipt:{event_id}:{subscription_id}:{expires}".encode(), hashlib.sha256).hexdigest()


def receipt_valid(settings: Settings, event_id: str, subscription_id: str, expires: int, token: str) -> bool:
    current = int(time.time())
    return current <= expires <= current + 86400 and hmac.compare_digest(
        receipt_mac(settings, event_id, subscription_id, expires), token)


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
    routing = agent.preferences.get("_crm_operator", {})
    if routing.get("role") not in {"operator", "admin"}:
        return False
    if routing.get("role") == "admin" and not routing.get("admin_opt_in", {}).get(business, False):
        return False
    return business in agent.allowed_businesses and bool(
        agent.preferences.get(business, {}).get(channel, True)
    )


def notification_allowed(session: Session, event: NotificationEvent, agent: Agent, channel: str) -> bool:
    if not preference(agent, event.business_id, channel):
        return False
    routing = agent.preferences.get("_crm_operator", {})
    if timestamp(event.created_at) < timestamp(routing.get("since")):
        return False
    crm = session.get(CRMConversation, (event.business_id, event.phone_number))
    if not crm or event.transition != crm.transition or crm.classification.get("in_human_inbox") is False:
        return False
    snapshot = crm.classification.get("notification", {})
    if snapshot.get("kind") == "message":
        read = session.get(ReadState, (event.business_id, event.phone_number, agent.profile))
        if read and read.last_message_id >= int(snapshot.get("message_id", 0)):
            return False
    if routing.get("role") == "admin":
        return True
    if event.business_id == "memos" and attention_queue(crm) != routing.get("queue", "retail"):
        return False
    return not crm.assigned_to or crm.assigned_to in (agent.profile, *routing.get("aliases", []))


def send_push(subscription: dict[str, Any], payload: dict[str, Any], settings: Settings) -> int:
    if not endpoint_allowed(subscription["endpoint"], settings):
        return 410
    try:
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode()) > 3500:
            # Body is derivable from reason/preview in the current worker; avoid
            # exceeding the encrypted Web Push budget with emoji-heavy text.
            encoded = json.dumps({k: v for k, v in payload.items() if k != "body"}, ensure_ascii=False)
        response = webpush(
            subscription_info={key: subscription[key] for key in ("endpoint", "keys")},
            data=encoded,
            vapid_private_key=vapid_identity(settings.vapid_private_key),
            vapid_claims={"sub": settings.vapid_subject},
            ttl=3600,
            headers={"Urgency": "high", "Topic": payload["conversation_tag"]},
            timeout=4,
        )
        return PushResult(int(response.status_code), response.headers.get("Retry-After", ""))
    except WebPushException as error:
        return PushResult(int(error.response.status_code), error.response.headers.get("Retry-After", "")) if error.response is not None else 503


def dispatch(settings: Settings, sessions: sessionmaker[Session], sender: Any = send_push) -> int:
    if not push_config(settings)["configured"]:
        return 0
    started = time.monotonic()
    # Serialize creation of outbox recipients across workers; per-delivery SKIP LOCKED below.
    with sessions.begin() as session:
        from sqlalchemy import text

        if not session.scalar(text("SELECT pg_try_advisory_xact_lock(724190031)")):
            return 0
        # A gateway 2xx is not device presentation. Current clients advertise
        # receipt support; retry at most twice more with the SAME event ID.
        # SW claims suppress duplicate display/sound and retry a lost receipt.
        unconfirmed = session.scalars(
            select(PushDelivery)
            .join(PushSubscription, PushSubscription.id == PushDelivery.subscription_id)
            .join(NotificationEvent, NotificationEvent.id == PushDelivery.event_id)
            .where(PushDelivery.status == "sent", PushDelivery.attempts < 3, PushDelivery.retry_at <= now(),
                   PushSubscription.active.is_(True), PushSubscription.subscription["_receipt_version"].as_integer() == 1,
                   NotificationEvent.created_at >= now() - NOTICE_MAX_AGE,
                   NotificationEvent.created_at >= cast(PushSubscription.subscription["_receipt_since"].as_string(), DateTime(timezone=True)))
            .order_by(PushDelivery.retry_at).limit(100).with_for_update(skip_locked=True, of=PushDelivery)
        )
        for delivery in unconfirmed:
            delivery.status = "pending"
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
            .where(PushSubscription.active.is_(True), PushDelivery.event_id.is_(None),
                   NotificationEvent.created_at >= now() - NOTICE_MAX_AGE)
            .order_by(NotificationEvent.created_at.desc(), NotificationEvent.id, PushSubscription.id)
            .limit(100)
        )
        for pending_event, recipient in missing_pairs:
            owner = session.get(Agent, recipient.profile)
            if session.get(PushDelivery, (pending_event.id, recipient.id)) is None:
                allowed = owner and notification_allowed(session, pending_event, owner, "web_push")
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
            candidate = session.scalar(
                select(PushDelivery)
                .where(PushDelivery.status == "pending", PushDelivery.retry_at <= now())
                .order_by(PushDelivery.retry_at, PushDelivery.event_id, PushDelivery.subscription_id)
                .limit(1)
            )
            if candidate is None:
                break
            event = session.get(NotificationEvent, candidate.event_id)
            if event:
                session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": attention_lock(event.business_id, event.phone_number)})
            # Match transfer's lock order: conversation first, then outbox row.
            delivery = session.scalar(select(PushDelivery).where(
                PushDelivery.event_id == candidate.event_id, PushDelivery.subscription_id == candidate.subscription_id,
                PushDelivery.status == "pending",
            ).with_for_update(skip_locked=True).execution_options(populate_existing=True))
            if delivery is None:
                continue
            sub = session.get(PushSubscription, delivery.subscription_id)
            agent = session.get(Agent, sub.profile) if sub else None
            if (
                not event
                or timestamp(event.created_at) < now() - NOTICE_MAX_AGE
                or not sub
                or not sub.active
                or sub.created_at > event.created_at
                or not agent
                or not notification_allowed(session, event, agent, "web_push")
            ):
                delivery.status = "disabled"
                continue
            delivery.attempts += 1
            try:
                expires = int(time.time()) + 86400
                topic = hashlib.sha256(f"{sub.profile}:{event.business_id}:{event.phone_number}".encode()).hexdigest()[:32]
                status = sender(
                    sub.subscription,
                    {
                        "id": event.id,
                        **notice_content(session, event),
                        "conversation_tag": topic,
                        "profile": sub.profile,
                        "subscription_id": sub.id,
                        "receipt": {"event_id": event.id, "subscription_id": sub.id, "expires": expires,
                                    "token": receipt_mac(settings, event.id, sub.id, expires)},
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
                delivery.retry_at = now() + timedelta(seconds=45 if delivery.attempts == 1 else 120)
                count += 1
            elif status in (400, 401, 403, 413) or delivery.attempts >= 6:
                delivery.status = "failed"
            else:
                delay = max((5, 15, 45, 120, 300)[min(delivery.attempts - 1, 4)], getattr(status, "retry_delay", 0))
                delivery.retry_at = now() + timedelta(seconds=delay)
            logger.info("push_delivery status=%s provider_status=%s attempt=%s", delivery.status, status, delivery.attempts)
    return count

