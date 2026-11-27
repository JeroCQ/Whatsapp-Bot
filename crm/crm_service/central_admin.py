"""Admin-only draft/usage API using the existing JWT, agents, DB and audit trail."""
import csv
import hashlib
import io
import json
from collections.abc import Callable, Iterator
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, delete, func, or_, select, text, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from crm_service import usage_store
from crm_service.central_config import legacy_defaults, legacy_references, public_business
from crm_service.central_schemas import AttemptInput, BusinessDraftInput, BusinessId, SharedDraftInput, SupportInput, TurnInput
from crm_service.classification import timestamp
from crm_service.models import (
    AuditEvent, BusinessConnectionReference, BusinessDraft, ModelUsageAttempt,
    SharedConfigDraft, SupportMinutes, UsageTurn, now,
)
from crm_service.security import Actor, decode_cursor, encode_cursor
from crm_service.settings import Settings
from crm_service.sync import lock_id


def digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def create_admin_router(settings: Settings, actor_dependency: Callable[..., Actor],
                        db_dependency: Callable[..., Iterator[Session]]) -> APIRouter:
    def admin(principal: Annotated[Actor, Depends(actor_dependency)]) -> Actor:
        if not principal.is_admin:
            raise HTTPException(403, "Admin access required")
        return principal

    def migrated(session: Annotated[Session, Depends(db_dependency)]) -> None:
        if not session.scalar(text("SELECT to_regclass('crm_business_drafts') IS NOT NULL")):
            raise HTTPException(503, "central_config_migration_not_applied")

    router = APIRouter(prefix="/v1/admin", dependencies=[Depends(admin), Depends(migrated)])

    def lock(session: Session, key: str) -> None:
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id("central:" + key)})

    def audit(session: Session, actor: Actor, business: str, action: str, details: dict[str, Any]) -> None:
        session.add(AuditEvent(business_id=business, phone_number="", profile=actor.profile,
                               action=action, details=details))

    def known(session: Session, business: str) -> None:
        if business not in settings.businesses and session.get(BusinessDraft, business) is None:
            raise HTTPException(404, "Unknown business")

    def usage_enabled(actor: Actor, business: str) -> None:
        if not settings.usage_collection_enabled:
            raise HTTPException(409, "usage_collection_disabled")
        actor.authorize(business)

    @router.get("/config")
    def config(session: Annotated[Session, Depends(db_dependency)]) -> dict[str, Any]:
        shared = session.get(SharedConfigDraft, "shared")
        drafts = {row.business_id: row for row in session.scalars(select(BusinessDraft))}
        return {
            "state": "draft", "runtime_source": "legacy_settings", "activation_available": False,
            "shared": {"version": shared.version if shared else 0, "values": shared.values if shared else {},
                       "legacy_defaults": legacy_defaults(settings)},
            "businesses": [{"business_id": business, "display_name": drafts[business].display_name if business in drafts else business,
                            "draft_exists": business in drafts, "state": "draft",
                            "runtime_source": "legacy_settings" if business in settings.businesses else "not_configured"}
                           for business in sorted(set(settings.businesses) | drafts.keys())],
            "usage_collection_enabled": settings.usage_collection_enabled, "cost_available": False,
        }

    @router.patch("/config/defaults")
    def shared_draft(body: SharedDraftInput, principal: Annotated[Actor, Depends(admin)],
                     session: Annotated[Session, Depends(db_dependency)]) -> dict[str, Any]:
        lock(session, "shared")
        row = session.get(SharedConfigDraft, "shared")
        if body.version != (row.version if row else 0):
            raise HTTPException(409, "draft_version_conflict")
        if row is None:
            row = SharedConfigDraft(id="shared", updated_by=principal.profile, version=0)
            session.add(row)
        row.values, row.version = body.values.model_dump(exclude_unset=True), row.version + 1
        row.updated_by, row.updated_at = principal.profile, now()
        session.flush()
        audit(session, principal, "", "config.shared_draft_saved", {"version": row.version})
        return {"state": "draft", "version": row.version, "values": row.values, "activation_available": False}

    @router.get("/config/businesses/{business_id}")
    def business(business_id: BusinessId, session: Annotated[Session, Depends(db_dependency)]) -> dict[str, Any]:
        known(session, business_id)
        return public_business(session, settings, business_id)

    @router.patch("/config/businesses/{business_id}")
    def save_business(business_id: BusinessId, body: BusinessDraftInput,
                      principal: Annotated[Actor, Depends(admin)],
                      session: Annotated[Session, Depends(db_dependency)]) -> dict[str, Any]:
        lock(session, business_id)
        row = session.get(BusinessDraft, business_id)
        if body.version != (row.version if row else 0):
            raise HTTPException(409, "draft_version_conflict")
        if row is None:
            row = BusinessDraft(business_id=business_id, updated_by=principal.profile, version=0)
            session.add(row)
        row.display_name = body.display_name
        row.whatsapp_phone_number_id = body.whatsapp_phone_number_id
        row.whatsapp_business_account_id = body.whatsapp_business_account_id
        row.overrides = body.overrides.model_dump(exclude_unset=True)
        with session.no_autoflush:
            authorized = {member["profile"] for member in public_business(session, settings, business_id)["memberships"]}
        if not set(body.operator_profiles) <= authorized:
            raise HTTPException(403, "operator_not_authorized_for_business")
        row.operator_profiles = sorted(set(body.operator_profiles))
        row.version += 1
        row.updated_by, row.updated_at = principal.profile, now()
        try:
            session.flush()
        except IntegrityError:
            raise HTTPException(409, "WhatsApp phone identifier already belongs to another draft") from None
        # Explicit draft save only; these pointers still resolve to legacy Settings,
        # contain no secrets. Runtime can use only a separately published pilot snapshot.
        references = legacy_references(settings, business_id)
        if body.connection_purposes is not None:
            if not set(body.connection_purposes) <= references.keys():
                raise HTTPException(409, "connection_not_available_on_server")
            session.execute(delete(BusinessConnectionReference).where(BusinessConnectionReference.business_id == business_id))
            references = {key: references[key] for key in body.connection_purposes}
        for purpose, reference in references.items():
            if session.get(BusinessConnectionReference, (business_id, purpose)) is None:
                session.add(BusinessConnectionReference(business_id=business_id, purpose=purpose, reference_key=reference))
        session.flush()
        audit(session, principal, business_id, "config.business_draft_saved", {"version": row.version})
        return public_business(session, settings, business_id)

    @router.post("/usage/turns")
    def record_turn(body: TurnInput, principal: Annotated[Actor, Depends(admin)],
                    session: Annotated[Session, Depends(db_dependency)]) -> dict[str, Any]:
        usage_enabled(principal, body.business_id)
        return usage_store.record_turn(session, body)

    @router.post("/usage/attempts")
    def record_attempt(body: AttemptInput, principal: Annotated[Actor, Depends(admin)],
                       session: Annotated[Session, Depends(db_dependency)]) -> dict[str, str]:
        usage_enabled(principal, body.business_id)
        return usage_store.record_attempt(session, body)

    @router.get("/commercial-metrics")
    def commercial_metrics(
        session: Annotated[Session, Depends(db_dependency)], business_id: BusinessId,
        date_from: date | None = None, date_to: date | None = None,
        attention_queue: Literal["retail", "wholesale"] | None = None,
        permission: Literal["yes", "no", "unknown"] | None = None,
    ) -> dict[str, Any]:
        from crm_service.commercial import CURSOR, handoff_metrics, report_sales, usage_metrics
        from crm_service.models import SyncCursor

        if business_id not in settings.customer_insights_businesses or not session.scalar(
            text("SELECT to_regclass('crm_commercial_events') IS NOT NULL")
        ):
            raise HTTPException(503, "commercial_metrics_unavailable")
        if attention_queue and business_id != "memos":
            raise HTTPException(422, "Attention queues apply only to Memo’s")
        zone = ZoneInfo("America/Bogota")
        through = date_to or now().astimezone(zone).date()
        since = date_from or through - timedelta(days=29)
        if through < since or (through - since).days > 90 or through == date.max:
            raise HTTPException(422, "Choose a date range of at most 91 days")
        start = datetime.combine(since, time.min, tzinfo=zone)
        end = datetime.combine(through + timedelta(days=1), time.min, tzinfo=zone)
        sender = settings.businesses[business_id].channel_id
        try:
            values = report_sales(session, business_id, start, end, attention_queue, sender, permission)
        except OverflowError:
            raise HTTPException(422, "Choose a shorter date range") from None
        cursor = session.get(SyncCursor, (business_id, CURSOR))
        return {**values, "date_from": since, "date_to": through, "activated_at": cursor.value.get("activated_at") if cursor else None,
                "usage": usage_metrics(session, business_id, start, end),
                "handoffs": handoff_metrics(session, business_id, start, end)}

    @router.get("/usage")
    def usage(session: Annotated[Session, Depends(db_dependency)], business_id: BusinessId | None = None,
              limit: int = Query(100, ge=1, le=1000), cursor: str | None = None,
              format: Literal["json", "csv"] = "json") -> Any:
        if business_id:
            known(session, business_id)
        statement = select(ModelUsageAttempt, UsageTurn.conversation_id).join(UsageTurn, and_(
            UsageTurn.business_id == ModelUsageAttempt.business_id, UsageTurn.turn_id == ModelUsageAttempt.turn_id))
        if business_id:
            statement = statement.where(ModelUsageAttempt.business_id == business_id)
        scope = "admin-usage:" + (business_id or "all")
        if cursor:
            value = decode_cursor(cursor, scope, settings)
            try:
                at, business, attempt = value["at"], value["business"], value["attempt"]
            except KeyError:
                raise HTTPException(400, "Invalid usage cursor") from None
            statement = statement.where(or_(ModelUsageAttempt.observed_at < timestamp(at), and_(
                ModelUsageAttempt.observed_at == timestamp(at),
                tuple_(ModelUsageAttempt.business_id, ModelUsageAttempt.attempt_id) > (business, attempt))))
        rows = session.execute(statement.order_by(ModelUsageAttempt.observed_at.desc(),
            ModelUsageAttempt.business_id, ModelUsageAttempt.attempt_id).limit(limit + 1)).all()
        fields = list(AttemptInput.model_fields)
        items = [{**{key: getattr(row, key) for key in fields if key != "conversation_id"},
                  "conversation_id": conversation, "cost": None, "cost_status": "unavailable"} for row, conversation in rows[:limit]]
        if format == "csv":
            def export() -> Iterator[str]:
                buffer = io.StringIO()
                writer = csv.writer(buffer)
                columns = [*fields, "cost", "cost_status"]
                writer.writerow(columns)
                yield buffer.getvalue()
                for item in items:
                    buffer.seek(0); buffer.truncate(0)
                    values = [item.get(key) for key in columns]
                    writer.writerow(["'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value for value in values])
                    yield buffer.getvalue()
            return StreamingResponse(export(), media_type="text/csv", headers={
                "Content-Disposition": 'attachment; filename="chatty-usage-page.csv"', "Cache-Control": "no-store"})
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1][0]
            next_cursor = encode_cursor({"at": last.observed_at.isoformat(), "business": last.business_id,
                                         "attempt": last.attempt_id}, scope, settings)
        attempts = select(func.count()).select_from(ModelUsageAttempt)
        turns = select(func.count(), func.count().filter(UsageTurn.outcome == "reply_accepted")).select_from(UsageTurn)
        if business_id:
            attempts = attempts.where(ModelUsageAttempt.business_id == business_id)
            turns = turns.where(UsageTurn.business_id == business_id)
        turn_count, replies = session.execute(turns).one()
        return {"items": items, "next_cursor": next_cursor, "model_attempts": session.scalar(attempts),
                "observed_turns": turn_count, "customer_reply_turns": replies, "cost": None,
                "collection_enabled": settings.usage_collection_enabled}

    @router.get("/support-minutes")
    def support(session: Annotated[Session, Depends(db_dependency)], business_id: BusinessId | None = None) -> dict[str, Any]:
        statement = select(SupportMinutes)
        if business_id:
            known(session, business_id)
            statement = statement.where(SupportMinutes.business_id == business_id)
        rows = session.scalars(statement.order_by(SupportMinutes.occurred_at.desc(), SupportMinutes.id).limit(100)).all()
        return {"items": [{"id": row.id, "business_id": row.business_id, "minutes": row.minutes,
                           "occurred_at": row.occurred_at, "category": row.category, "note": row.note,
                           "created_by": row.created_by} for row in rows], "limit": 100}

    @router.post("/support-minutes", status_code=201)
    def record_support(body: SupportInput, principal: Annotated[Actor, Depends(admin)],
                       session: Annotated[Session, Depends(db_dependency)]) -> dict[str, str]:
        known(session, body.business_id)
        lock(session, "support:" + str(body.id))
        fingerprint = digest({**body.model_dump(mode="json"), "created_by": principal.profile})
        row = session.get(SupportMinutes, str(body.id))
        if row and row.request_hash != fingerprint:
            raise HTTPException(409, "support_entry_identity_conflict")
        if row is None:
            row = SupportMinutes(**body.model_dump(exclude={"id"}), id=str(body.id),
                                 created_by=principal.profile, request_hash=fingerprint)
            session.add(row)
            session.flush()
            audit(session, principal, body.business_id, "support.minutes_recorded", {"id": row.id, "minutes": row.minutes})
        return {"id": row.id, "status": "recorded"}

    return router
