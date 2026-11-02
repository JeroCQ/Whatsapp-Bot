import hashlib
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from crm_service.classification import EPOCH, classify, timestamp
from crm_service.models import (
    CRMConversation,
    NotificationEvent,
    SourceConversation,
    SourceMessage,
    SyncCursor,
    SyncStatus,
    now,
)
from crm_service.settings import Business, Settings
from crm_service.source import SourceAdapter, SourceFailure

logger = logging.getLogger(__name__)


def lock_id(business_id: str) -> int:
    return int.from_bytes(hashlib.sha256(f"crm-sync:{business_id}".encode()).digest()[:8], "big", signed=True)


class Synchronizer:
    def __init__(
        self,
        settings: Settings,
        sessions: sessionmaker[Session],
        factory: Callable[[Business], SourceAdapter] = SourceAdapter,
    ):
        self.settings, self.sessions, self.factory = settings, sessions, factory

    def run(self) -> dict[str, str]:
        results = {}
        for business in self.settings.businesses.values():
            try:
                results[business.id] = self.business(business)
            except Exception as error:
                safe = str(error) if isinstance(error, SourceFailure) else type(error).__name__
                # Never log exception bodies, URLs, phone numbers, message text or credentials.
                logger.warning("sync_failed business=%s code=%s", business.id, safe)
                with self.sessions.begin() as session:
                    status = session.get(SyncStatus, business.id)
                    if status is None:
                        status = SyncStatus(business_id=business.id)
                        session.add(status)
                    status.last_attempt, status.last_error = now(), safe
                results[business.id] = "failed"
        return results

    def business(self, business: Business) -> str:
        adapter = None
        try:
            with self.sessions.begin() as session:
                locked = session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_id(business.id)}
                )
                if not locked:
                    return "already_running"
                status = session.get(SyncStatus, business.id)
                if status is None:
                    status = SyncStatus(business_id=business.id, baseline_complete=False)
                    session.add(status)
                adapter = self.factory(business)
                for table in ("customers", "conversation_states", "handoff_deliveries"):
                    self.sweep(session, business, adapter, table)
                self.messages(session, business, adapter)
                self.reconcile_messages(session, business, adapter)
                self.project(session, business, sorted(adapter.warnings), status.baseline_complete)
                status.baseline_complete = True
                status.last_attempt = status.last_success = now()
                status.last_error = None
                status.warnings = sorted(adapter.warnings)
            return "ok"
        finally:
            if adapter:
                adapter.close()

    def cursor(self, session: Session, business: str, resource: str) -> SyncCursor:
        row = session.get(SyncCursor, (business, resource))
        if row is None:
            row = SyncCursor(business_id=business, resource=resource, value={})
            session.add(row)
            session.flush()
        return row

    def conversation(self, session: Session, business: str, phone: Any) -> SourceConversation:
        if not isinstance(phone, str) or not phone or len(phone) > 200:
            raise SourceFailure("invalid_source_identity")
        row = session.get(SourceConversation, (business, phone))
        if row is None:
            row = SourceConversation(business_id=business, phone_number=phone)
            session.add(row)
            session.flush()
        return row

    def sweep(self, session: Session, business: Business, adapter: SourceAdapter, table: str) -> None:
        cursor = self.cursor(session, business.id, table)
        after = None
        # Mutable tables have no trustworthy update cursor in older deployments.
        # Reconcile these by keyset each poll; never rely on last_interaction for updates.
        for _ in range(self.settings.max_pages):
            rows = adapter.page(table, after=after, limit=self.settings.page_size)
            if not rows:
                cursor.value = {"sweep_complete": True, "last_key": after}
                cursor.updated_at = now()
                return
            for item in rows:
                if table == "handoff_deliveries" and item.get("business_id") != business.id:
                    raise SourceFailure("source_business_mismatch")
                phone = item.get("phone_number")
                if after is not None and (not isinstance(phone, str) or phone <= after):
                    raise SourceFailure("source_non_monotonic_page")
                conv = self.conversation(session, business.id, phone)
                if table == "customers":
                    conv.customer, conv.name = item, str(item.get("name") or "")
                elif table == "conversation_states":
                    conv.state = item
                else:
                    conv.handoff = item
                after = phone
        raise SourceFailure("source_sweep_page_limit")

    def upsert_message(self, session: Session, business: str, item: dict[str, Any]) -> None:
        self.conversation(session, business, item.get("phone_number"))
        metadata = item.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        if metadata.get("business_id") and metadata["business_id"] != business:
            raise SourceFailure("message_business_mismatch")
        key = item.get("event_key") or None
        row = session.get(SourceMessage, (business, int(item["id"])))
        if row is None and key:
            row = session.scalar(
                select(SourceMessage).where(
                    SourceMessage.business_id == business, SourceMessage.event_key == key
                )
            )
        if row is not None and row.phone_number != item["phone_number"]:
            raise SourceFailure("source_message_identity_changed")
        if row is None:
            row = SourceMessage(business_id=business, source_id=int(item["id"]))
            session.add(row)
        row.phone_number, row.role = item["phone_number"], str(item["role"])
        row.content = str(item.get("content") or "")
        row.created_at = timestamp(item.get("created_at"))
        if row.created_at == EPOCH:
            raise SourceFailure("invalid_message_timestamp")
        row.message_metadata, row.event_key = metadata, key
        session.flush()

    def messages(self, session: Session, business: Business, adapter: SourceAdapter) -> None:
        cursor = self.cursor(session, business.id, "message_logs")
        after = int(cursor.value.get("id", 0))
        for _ in range(self.settings.max_pages):
            rows = adapter.page("message_logs", after=after, limit=self.settings.page_size)
            if not rows:
                cursor.value, cursor.updated_at = {"id": after}, now()
                return
            for item in rows:
                if int(item["id"]) <= after:
                    raise SourceFailure("source_non_monotonic_messages")
                self.upsert_message(session, business.id, item)
                after = int(item["id"])
        raise SourceFailure("source_message_page_limit")

    def reconcile_messages(self, session: Session, business: Business, adapter: SourceAdapter) -> None:
        # Bounded rolling sweep picks up edits (e.g. transcriptions) to old IDs.
        cursor = self.cursor(session, business.id, "message_reconciliation")
        after = int(cursor.value.get("id", 0))
        rows = adapter.page("message_logs", after=after, limit=self.settings.page_size)
        previous = after
        for item in rows:
            current = int(item["id"])
            if current <= previous:
                raise SourceFailure("source_non_monotonic_reconciliation")
            self.upsert_message(session, business.id, item)
            previous = current
        cursor.value, cursor.updated_at = {"id": previous if rows else 0}, now()

    def project(self, session: Session, business: Business, warnings: list[str], baseline: bool) -> None:
        for source in session.scalars(
            select(SourceConversation).where(SourceConversation.business_id == business.id)
        ):
            rows = session.scalars(
                select(SourceMessage).where(
                    SourceMessage.business_id == business.id,
                    SourceMessage.phone_number == source.phone_number,
                )
            ).all()
            messages = [
                {
                    "id": m.source_id,
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at,
                    "metadata": m.message_metadata,
                }
                for m in rows
            ]
            result = classify(source.state, messages, source.handoff, now(), warnings)
            source.warnings = result["data_quality_warnings"]
            row = session.get(CRMConversation, (business.id, source.phone_number))
            previous = row.bucket if row else None
            if row is None:
                row = CRMConversation(
                    business_id=business.id, phone_number=source.phone_number, transition=0, version=1
                )
                session.add(row)
            entering = result["bucket"] == "awaiting_human" and previous != "awaiting_human"
            if entering:
                row.transition += 1
            row.bucket, row.classification, row.sort_key = result["bucket"], result, result["sort_key"]
            session.flush()
            if baseline and entering and self.settings.notifications_enabled:
                link = "/chats?" + urlencode(
                    {
                        "business_id": business.id,
                        "phone_number": source.phone_number,
                        "bucket": "awaiting_human",
                    }
                )
                session.add(
                    NotificationEvent(
                        business_id=business.id,
                        phone_number=source.phone_number,
                        transition=row.transition,
                        link=link,
                    )
                )
