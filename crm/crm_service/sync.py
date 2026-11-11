import hashlib
import logging
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Protocol
from urllib.parse import urlencode

from sqlalchemy import or_, select, text, true
from sqlalchemy.orm import Session, sessionmaker

from crm_service.budget import BudgetExhausted, ReadBudget
from crm_service.classification import EPOCH, classify, timestamp
from crm_service.delivery import project_message, visible_message_clause
from crm_service.local_targets import require_disposable_source
from crm_service.models import (
    Agent,
    CRMConversation,
    NotificationEvent,
    SourceConversation,
    SourceMessage,
    SyncCursor,
    SyncStatus,
    now,
)
from crm_service.routing import QUEUE_ROUTING_VERSION, project_queue
from crm_service.settings import Business, Settings
from crm_service.source import SourceFailure

logger = logging.getLogger(__name__)


class Reader(Protocol):
    warnings: set[str]

    def page(self, table: str, *, after: str | int | None, limit: int) -> list[dict[str, Any]]: ...
    def close(self) -> None: ...


def lock_id(business_id: str) -> int:
    return int.from_bytes(hashlib.sha256(f"crm-sync:{business_id}".encode()).digest()[:8], "big", signed=True)


class Synchronizer:
    def __init__(
        self,
        settings: Settings,
        sessions: sessionmaker[Session],
        factory: Callable[[Business], Reader] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.settings, self.sessions, self.factory = settings, sessions, factory
        self.clock = clock

    def run(self, business_id: str | None = None, *, force: bool = False) -> dict[str, str]:
        if business_id is not None and business_id not in self.settings.businesses:
            raise ValueError("Unknown business")
        results = {}
        for business in self.settings.businesses.values():
            if business_id is not None and business.id != business_id:
                continue
            with self.sessions() as session:
                prior = session.get(SyncStatus, business.id)
                if prior and prior.next_retry_at and timestamp(prior.next_retry_at) > now() and not force:
                    results[business.id] = "backoff"
                    continue
            budget = ReadBudget(self.settings, self.clock)
            live_committed = False
            try:
                if prior and prior.baseline_complete and self.settings.source_mode == "bot_api":
                    try:
                        live = self.live_business(business, budget)
                        live_committed = live == "ok"
                    except SourceFailure as error:
                        if str(error) != "bot_source_phone_filter_unavailable":
                            raise
                        live = "ok"  # Compatible periodic reconciliation for older exporters.
                    if live != "ok":
                        results[business.id] = live
                        continue
                    # Mutable tables still get a resumable full reconciliation,
                    # but never in front of the committed incremental live pass.
                    with self.sessions() as session:
                        sweep = session.get(SyncCursor, (business.id, "mutable_refresh"))
                        due = not sweep or timestamp(sweep.value.get("next_at")) <= now()
                    if not due:
                        results[business.id] = live
                        continue
                    try:
                        budget.check()
                    except BudgetExhausted:
                        results[business.id] = "partial"
                        continue
                results[business.id] = self.business(business, budget)
                if results[business.id] == "ok":
                    with self.sessions.begin() as session:
                        sweep = self.cursor(session, business.id, "mutable_refresh")
                        sweep.value = {"next_at": (now() + timedelta(seconds=60)).isoformat()}
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
                    status.failures = (status.failures or 0) + 1
                    delay = min(
                        self.settings.backoff_max_seconds,
                        self.settings.backoff_base_seconds * 2 ** min(status.failures - 1, 12),
                    )
                    if live_committed:
                        # Historical reconciliation failed after current messages
                        # were committed. Back off that sweep, not live traffic.
                        sweep = self.cursor(session, business.id, "mutable_refresh")
                        sweep.value = {"next_at": (now() + timedelta(seconds=delay)).isoformat()}
                        status.next_retry_at = None
                        status.run_state = "partial"
                    else:
                        status.next_retry_at = now() + timedelta(seconds=delay)
                        status.run_state = "failed"
                    status.metrics = budget.metrics()
                results[business.id] = "partial" if live_committed else "failed"
        return results

    def reader(self, business: Business, budget: ReadBudget) -> Reader:
        if self.factory:
            return self.factory(business)
        if self.settings.source_mode == "disabled":
            raise SourceFailure("source_access_disabled")
        if self.settings.source_mode == "bot_api":
            from crm_service.bot_source import BotSourceAdapter

            return BotSourceAdapter(business, budget=budget, test_mode=self.settings.bot_test_mode)
        require_disposable_source(business.readonly_database_url)
        from crm_service.source_postgres import PostgresSourceAdapter

        return PostgresSourceAdapter(business, budget=budget)

    def live_business(self, business: Business, budget: ReadBudget) -> str:
        """Commit new messages + their own fresh contact/state before broad scans."""
        adapter = self.reader(business, budget)
        try:
            with self.sessions.begin() as session:
                self.local_timeout(session, budget)
                if not session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_id(business.id)}
                ):
                    return "already_running"
                status = session.get(SyncStatus, business.id)
                if status is None or not status.baseline_complete:
                    return "partial"
                try:
                    live = self.cursor(session, business.id, "live_cycle")
                    if not live.value.get("phase"):
                        self.messages(session, business, adapter, budget)
                        live.value = {"phase": 1}
                    pending = session.scalars(
                        select(SourceConversation)
                        .where(
                            SourceConversation.business_id == business.id,
                            SourceConversation.dirty.is_(True),
                        )
                        .order_by(SourceConversation.phone_number)
                        .limit(self.settings.sync_max_projections)
                    ).all()
                    for source in pending:
                        start = (
                            int(live.value.get("table", 0))
                            if live.value.get("phone") == source.phone_number
                            else 0
                        )
                        tables = ("customers", "handoff_deliveries", "conversation_states")
                        for index in range(start, len(tables)):
                            table = tables[index]
                            budget.before_page()
                            read = getattr(adapter, "conversation", None)
                            if not callable(read):
                                raise SourceFailure("bot_source_phone_filter_unavailable")
                            if self.factory:
                                with budget.request():
                                    rows = read(table, source.phone_number)
                                    budget.record_rows(len(rows))
                            else:
                                rows = read(table, source.phone_number)
                            for item in rows:
                                if item.get("phone_number") != source.phone_number:
                                    raise SourceFailure("source_conversation_mismatch")
                                if table == "customers":
                                    source.customer, source.name = item, str(item.get("name") or "")
                                elif table == "conversation_states":
                                    source.state = item
                                else:
                                    if item.get("business_id") != business.id:
                                        raise SourceFailure("source_business_mismatch")
                                    source.handoff = item
                            live.value = {"phase": 1, "phone": source.phone_number, "table": index + 1}
                        self.project(
                            session, business, sorted(adapter.warnings), True, budget, [source.phone_number]
                        )
                        live.value = {"phase": 1}
                    live.value = {"phase": 0}
                    status.run_state = "ok"
                except BudgetExhausted:
                    status.run_state = "partial"
                status.last_attempt = now()
                if status.run_state == "ok":
                    status.last_success = now()
                status.last_error, status.failures, status.next_retry_at = None, 0, None
                status.metrics, status.warnings = budget.metrics(), sorted(adapter.warnings)
            return status.run_state
        finally:
            adapter.close()

    def business(self, business: Business, budget: ReadBudget | None = None) -> str:
        budget = budget or ReadBudget(self.settings, self.clock)
        adapter = None
        try:
            with self.sessions.begin() as session:
                locked = session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_id(business.id)}
                )
                if not locked:
                    return "already_running"
                self.local_timeout(session, budget)
                status = session.get(SyncStatus, business.id)
                if status is None:
                    status = SyncStatus(business_id=business.id, baseline_complete=False)
                    session.add(status)
                cycle = self.cursor(session, business.id, "cycle")
                phase = int(cycle.value.get("phase", 0))
                try:
                    adapter = self.reader(business, budget)
                    stages = ("customers", "conversation_states", "handoff_deliveries", "message_logs")
                    while phase < 4:
                        self.local_timeout(session, budget)
                        if phase < 3:
                            self.sweep(session, business, adapter, stages[phase], budget)
                        else:
                            self.messages(session, business, adapter, budget)
                        phase += 1
                        cycle.value = {"phase": phase}
                    if phase == 4:
                        if status.baseline_complete:
                            self.reconcile_messages(session, business, adapter, budget)
                        phase = 5
                        cycle.value = {"phase": phase}
                    self.project(
                        session, business, sorted(adapter.warnings), status.baseline_complete, budget
                    )
                    budget.check()
                    status.baseline_complete = True
                    cycle.value = {"phase": 0}
                    status.run_state = "ok"
                except BudgetExhausted:
                    status.run_state = "partial"
                status.last_attempt = now()
                if status.run_state == "ok":
                    status.last_success = now()
                status.last_error = None
                status.failures, status.next_retry_at = 0, None
                status.metrics = budget.metrics()
                status.warnings = sorted(adapter.warnings) if adapter else []
            return status.run_state
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

    def local_timeout(self, session: Session, budget: ReadBudget) -> None:
        milliseconds = max(1, int(budget.timeout_seconds() * 1000))
        session.execute(
            text(
                "SELECT set_config('statement_timeout', :ms, true), "
                "set_config('lock_timeout', :lock_ms, true)"
            ),
            {"ms": str(milliseconds), "lock_ms": str(min(milliseconds, 1000))},
        )

    def conversation(self, session: Session, business: str, phone: Any) -> SourceConversation:
        if not isinstance(phone, str) or not phone or len(phone) > 200:
            raise SourceFailure("invalid_source_identity")
        row = session.get(SourceConversation, (business, phone))
        if row is None:
            row = SourceConversation(business_id=business, phone_number=phone)
            session.add(row)
            session.flush()
        return row

    def page(
        self, adapter: Reader, table: str, after: str | int | None, budget: ReadBudget
    ) -> list[dict[str, Any]]:
        budget.before_page()
        if self.factory:
            with budget.request():
                rows = adapter.page(table, after=after, limit=self.settings.page_size)
                budget.record_rows(len(rows))
            return rows
        return adapter.page(table, after=after, limit=self.settings.page_size)

    def sweep(
        self, session: Session, business: Business, adapter: Reader, table: str, budget: ReadBudget
    ) -> None:
        cursor = self.cursor(session, business.id, table)
        after = cursor.value.get("after") if not cursor.value.get("complete") else None
        # Mutable tables have no trustworthy update cursor in older deployments.
        # Reconcile these by keyset each poll; never rely on last_interaction for updates.
        while True:
            self.local_timeout(session, budget)
            rows = self.page(adapter, table, after, budget)
            if not rows:
                cursor.value = {"complete": True, "after": after}
                cursor.updated_at = now()
                return
            for item in rows:
                budget.check()
                if table == "handoff_deliveries" and item.get("business_id") != business.id:
                    raise SourceFailure("source_business_mismatch")
                phone = item.get("phone_number")
                if after is not None and (not isinstance(phone, str) or phone <= after):
                    raise SourceFailure("source_non_monotonic_page")
                conv = self.conversation(session, business.id, phone)
                if table == "customers":
                    if conv.customer != item:
                        conv.customer, conv.name, conv.dirty = item, str(item.get("name") or ""), True
                elif table == "conversation_states":
                    if conv.state != item:
                        conv.state, conv.dirty = item, True
                else:
                    if conv.handoff != item:
                        conv.handoff, conv.dirty = item, True
                after = phone
                cursor.value, cursor.updated_at = {"after": after, "complete": False}, now()
            cursor.value, cursor.updated_at = {"after": after, "complete": False}, now()

    def upsert_message(self, session: Session, business: str, item: dict[str, Any]) -> None:
        conversation = self.conversation(session, business, item.get("phone_number"))
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
        changed = (
            row.content != str(item.get("content") or "")
            or row.role != str(item["role"])
            or row.message_metadata != metadata
            or timestamp(row.created_at) != timestamp(item.get("created_at"))
            or row.event_key != key
        )
        if changed:
            conversation.dirty = True
        row.phone_number, row.role = item["phone_number"], str(item["role"])
        row.content = str(item.get("content") or "")
        row.created_at = timestamp(item.get("created_at"))
        if row.created_at == EPOCH:
            raise SourceFailure("invalid_message_timestamp")
        row.message_metadata, row.event_key = metadata, key
        session.flush()
        project_message(session, row)

    def messages(self, session: Session, business: Business, adapter: Reader, budget: ReadBudget) -> None:
        cursor = self.cursor(session, business.id, "message_logs")
        after = int(cursor.value.get("id", 0))
        while True:
            self.local_timeout(session, budget)
            rows = self.page(adapter, "message_logs", after, budget)
            if not rows:
                cursor.value, cursor.updated_at = {"id": after}, now()
                return
            for item in rows:
                budget.check()
                if int(item["id"]) <= after:
                    raise SourceFailure("source_non_monotonic_messages")
                self.upsert_message(session, business.id, item)
                after = int(item["id"])
                cursor.value, cursor.updated_at = {"id": after}, now()
            cursor.value, cursor.updated_at = {"id": after}, now()

    def reconcile_messages(
        self, session: Session, business: Business, adapter: Reader, budget: ReadBudget
    ) -> None:
        # Bounded rolling sweep picks up edits (e.g. transcriptions) to old IDs.
        cursor = self.cursor(session, business.id, "message_reconciliation")
        if cursor.value.get("next_at") and timestamp(cursor.value["next_at"]) > now():
            return
        after = int(cursor.value.get("id", 0))
        rows = self.page(adapter, "message_logs", after, budget)
        previous = after
        for item in rows:
            budget.check()
            current = int(item["id"])
            if current <= previous:
                raise SourceFailure("source_non_monotonic_reconciliation")
            self.upsert_message(session, business.id, item)
            previous = current
            cursor.value, cursor.updated_at = {"id": previous}, now()
        cursor.value = {
            "id": previous if rows else 0,
            "next_at": (now() + timedelta(seconds=self.settings.reconcile_interval)).isoformat(),
        }
        cursor.updated_at = now()

    def project(
        self,
        session: Session,
        business: Business,
        warnings: list[str],
        baseline: bool,
        budget: ReadBudget,
        phones: list[str] | None = None,
    ) -> None:
        count = 0
        for source in session.scalars(
            select(SourceConversation)
            .outerjoin(
                CRMConversation,
                (CRMConversation.business_id == SourceConversation.business_id)
                & (CRMConversation.phone_number == SourceConversation.phone_number),
            )
            .where(
                SourceConversation.business_id == business.id,
                SourceConversation.phone_number.in_(phones) if phones is not None else true(),
                or_(
                    SourceConversation.dirty.is_(True),
                    CRMConversation.classification["classification_version"].as_integer().is_distinct_from(3),
                    (SourceConversation.business_id == "memos") & CRMConversation.classification["queue_routing_version"].as_integer().is_distinct_from(QUEUE_ROUTING_VERSION),
                ),
            )
            .order_by(SourceConversation.dirty.desc(), SourceConversation.phone_number)
            .limit(self.settings.sync_max_projections + 1)
        ):
            budget.check()
            self.local_timeout(session, budget)
            if count >= self.settings.sync_max_projections:
                raise BudgetExhausted("projection_budget")
            rows = session.scalars(
                select(SourceMessage)
                .where(
                    SourceMessage.business_id == business.id,
                    SourceMessage.phone_number == source.phone_number,
                    visible_message_clause(),
                )
                .limit(self.settings.sync_max_history_messages + 1)
            ).all()
            if len(rows) > self.settings.sync_max_history_messages:
                raise SourceFailure("conversation_history_limit_requires_review")
            messages = [
                {
                    "id": m.source_id,
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at,
                    "metadata": {
                        **m.message_metadata,
                        **({"delivery_status": m.delivery_status} if m.delivery_status else {}),
                    },
                }
                for m in rows
            ]
            row = session.get(CRMConversation, (business.id, source.phone_number))
            result = classify(source.state, messages, source.handoff, now(), warnings,
                              previous=row.classification if row else None)
            prior_human = bool(row and row.bucket in {"awaiting_human", "human_active"} and row.classification.get("in_human_inbox") is not False)
            entering = result["bucket"] == "awaiting_human" and result.get("in_human_inbox", True) and not prior_human
            incoming_id = max((int(m["id"]) for m in messages if m["role"] == "user"), default=0)
            previous_incoming = row.classification.get("notification_incoming_id", row.classification.get("last_message_id")) if row else None
            new_incoming = bool(
                baseline and previous_incoming is not None and incoming_id > previous_incoming
                and result.get("in_human_inbox") and source.state.get("is_paused") is True
                and result.get("unanswered_customer_count", 0) > 0
            )
            # Establish the watermark without replaying a backlog on upgrade/backfill.
            result["notification_incoming_id"] = incoming_id
            if business.id == "memos":
                routing = project_queue(source.state, row.classification if row else {}, entering)
                queue = routing["attention_queue"]
                result.update(routing)
                if row and row.classification.get("attention_queue") != queue:
                    row.version += 1
                    row.assigned_to = None
                if row and entering:
                    row.assigned_to = None
                if row and not row.assigned_to:
                    for agent in session.scalars(select(Agent).order_by(Agent.profile)):
                        routing = agent.preferences.get("_crm_operator", {})
                        if business.id in agent.allowed_businesses and routing.get("role") == "operator" and routing.get("queue") == queue:
                            row.assigned_to = agent.profile
                            break
            source.warnings = result["data_quality_warnings"]
            source.dirty = False
            count += 1
            if row is None:
                row = CRMConversation(
                    business_id=business.id, phone_number=source.phone_number, transition=0, version=1
                )
                session.add(row)
            if entering or new_incoming:
                row.transition += 1
            row.bucket, row.classification, row.sort_key = result["bucket"], result, result["sort_key"]
            session.flush()
            if baseline and (entering or new_incoming) and self.settings.notifications_enabled:
                link = "/chats?" + urlencode({"chat": f"{business.id}:{source.phone_number}", "bucket": "needs_reply"})
                session.add(
                    NotificationEvent(
                        business_id=business.id,
                        phone_number=source.phone_number,
                        transition=row.transition,
                        link=link,
                    )
                )
            budget.check()
