"""Project verified WhatsApp choices from the existing durable message stream."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from crm_service.classification import EPOCH, timestamp
from crm_service.models import PromotionPermission, PromotionPermissionEvent, SourceMessage

QUESTION = (
    "¿Quieres recibir promociones de este negocio por WhatsApp? "
    "Puedes darte de baja cuando quieras escribiendo BAJA PROMOCIONES."
)
VERSION = "promotions-v1"
BLACK_WEEK_VERSION = "black-week-2026-v1"
BLACK_WEEK_SENDERS = {"tanaka": "1279018015292550", "velvet": "1330184450175963"}
BLACK_WEEK_CHOICES = {
    "granted": ("Quiero promociones exclusivas", "Seguir recibiendo promos"),
    "revoked": ("Prefiero perder las promociones", "Prefiero perder promos"),
}


def apply_event(session, event, question):
    key = (event.business_id, event.sender_id, event.phone_number)
    # Create the singleton before taking its row lock, even on concurrent imports.
    session.execute(insert(PromotionPermission).values(
        business_id=key[0], sender_id=key[1], phone_number=key[2], status="unknown",
    ).on_conflict_do_nothing())
    current = session.get(PromotionPermission, key, with_for_update=True, populate_existing=True)
    event.validated = True
    if current.event_at is not None:
        if event.event_at < current.event_at:
            return
        if event.event_at == current.event_at and event.status == "granted":
            return
        if (question and event.status == "granted" and current.status in {"denied", "revoked"}
                and timestamp(question.created_at) <= current.event_at):
            return  # A button on the old question cannot undo a later refusal.
    current.status, current.event_at, current.event_id = event.status, event.event_at, event.inbound_id


def find_question(session, business, phone, provider_id):
    return session.scalar(select(SourceMessage).where(
        SourceMessage.business_id == business,
        SourceMessage.phone_number == phone,
        SourceMessage.provider_message_id == provider_id,
        SourceMessage.role == "asesor",
        SourceMessage.content == QUESTION,
        SourceMessage.delivery_status.in_(("accepted", "delivered", "read")),
    ).limit(1))


def resolve_event(session, event):
    if event.version == BLACK_WEEK_VERSION:
        # The signed reply references the original external send. Because it
        # originated outside Chatty, there is no local outbound command to find.
        apply_event(session, event, None)
        return
    question = None
    if event.question_id:
        question = find_question(session, event.business_id, event.phone_number, event.question_id)
        if question is None or timestamp(question.created_at) > event.event_at + timedelta(minutes=1):
            return
    apply_event(session, event, question)


def project_permission_message(session, message):
    meta = message.message_metadata
    if message.role == "system" and meta.get("source") == "promotion_permission":
        choice = meta.get("permission_status")
        sender_id, inbound_id = meta.get("channel_id"), meta.get("source_event_id")
        question_id = meta.get("permission_question_id")
        version = meta.get("permission_version")
        event_at = timestamp(meta.get("received_at"))
        if (version not in (VERSION, BLACK_WEEK_VERSION) or not isinstance(choice, str)
                or choice not in {"granted", "denied", "revoked"}
                or not isinstance(sender_id, str) or sender_id != BLACK_WEEK_SENDERS.get(message.business_id)
                or meta.get("business_id") != message.business_id
                or not isinstance(inbound_id, str) or not 0 < len(inbound_id) <= 300 or event_at == EPOCH
                or (question_id is not None and (not isinstance(question_id, str) or not 0 < len(question_id) <= 300))
                or (choice == "denied" and question_id is None)):
            return
        if version == BLACK_WEEK_VERSION and (
                question_id is None or question_id == inbound_id
                or message.content not in BLACK_WEEK_CHOICES.get(choice, ())):
            return
        event = session.get(PromotionPermissionEvent, (message.business_id, sender_id, inbound_id))
        if event is None:
            event = PromotionPermissionEvent(
                business_id=message.business_id, sender_id=sender_id, inbound_id=inbound_id,
                phone_number=message.phone_number, status=choice, question_id=question_id,
                version=version, event_at=event_at, recorded_at=message.created_at,
                evidence=message.content[:160], validated=False,
            )
            session.add(event)
        if not event.validated:
            resolve_event(session, event)
        return

    if not message.provider_message_id or not (
        message.role == "asesor" and message.content == QUESTION or message.is_delivery_event
    ):
        return
    question = find_question(session, message.business_id, message.phone_number, message.provider_message_id)
    if question is None:
        return
    for event in session.scalars(select(PromotionPermissionEvent).where(
        PromotionPermissionEvent.business_id == message.business_id,
        PromotionPermissionEvent.phone_number == message.phone_number,
        PromotionPermissionEvent.question_id == message.provider_message_id,
        PromotionPermissionEvent.validated.is_(False),
    ).order_by(PromotionPermissionEvent.event_at, PromotionPermissionEvent.inbound_id)):
        if timestamp(question.created_at) <= event.event_at + timedelta(minutes=1):
            apply_event(session, event, question)
