import hashlib
import hmac
import json
import logging
from collections.abc import Iterator
from datetime import timedelta
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import and_, func, or_, select, text, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from crm_service.classification import timestamp
from crm_service.db import database
from crm_service.models import (
    Agent,
    AuditEvent,
    CRMConversation,
    Note,
    NotificationEvent,
    PushSubscription,
    ReadState,
    SourceConversation,
    SourceMessage,
    SyncStatus,
    Task,
    now,
)
from crm_service.notifications import endpoint_allowed, endpoint_hash, preference
from crm_service.schemas import (
    Bucket,
    CRMUpdate,
    NoteInput,
    PreferencesInput,
    PushInput,
    ReadInput,
    TaskInput,
    TaskUpdate,
)
from crm_service.security import Actor, authenticate, decode_cursor, encode_cursor
from crm_service.settings import Settings
from crm_service.sync import Synchronizer, lock_id


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    engine, sessions = database(settings)
    app = FastAPI(title="Independent WhatsApp CRM", version="0.1.0", docs_url=None, redoc_url=None)
    app.state.settings, app.state.sessions, app.state.engine = settings, sessions, engine

    def db() -> Iterator[Session]:
        with sessions.begin() as session:
            yield session

    DB = Annotated[Session, Depends(db)]

    def actor(request: Request, session: DB) -> Actor:
        principal = authenticate(request, settings)
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id("actor:" + principal.profile)}
        )
        # Identity comes exclusively from the signed server JWT, never from a query/body.
        agent = session.get(Agent, principal.profile)
        if agent is None:
            session.add(Agent(profile=principal.profile, allowed_businesses=list(principal.businesses)))
        else:
            agent.allowed_businesses = list(principal.businesses)
        session.flush()
        return principal

    Auth = Annotated[Actor, Depends(actor)]

    def audit(
        session: Session,
        principal: Actor,
        business: str,
        phone: str,
        action: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        session.add(
            AuditEvent(
                business_id=business,
                phone_number=phone,
                profile=principal.profile,
                action=action,
                details=details or {},
            )
        )

    def conversation(
        session: Session, principal: Actor, business: str, phone: str, lock: bool = False
    ) -> tuple[SourceConversation, CRMConversation]:
        principal.authorize(business)
        query = select(CRMConversation).where(
            CRMConversation.business_id == business, CRMConversation.phone_number == phone
        )
        if lock:
            query = query.with_for_update()
        crm = session.scalar(query)
        source = session.get(SourceConversation, (business, phone))
        if not source or not crm:
            raise HTTPException(404, "Conversation not found")
        return source, crm

    def check_assignment(session: Session, principal: Actor, business: str, assignee: str | None) -> None:
        if assignee:
            agent = session.get(Agent, assignee)
            if not agent or business not in agent.allowed_businesses:
                raise HTTPException(422, "Assignee has no registered access to this business")

    def display(
        session: Session, principal: Actor, source: SourceConversation, crm: CRMConversation
    ) -> dict[str, Any]:
        classification = dict(crm.classification)
        since = classification.get("attention_since")
        classification["waiting_seconds"] = (
            max(0, int((now() - timestamp(since)).total_seconds())) if since else 0
        )
        classification.pop("sort_key", None)
        read = session.get(ReadState, (source.business_id, source.phone_number, principal.profile))
        return {
            "business_id": source.business_id,
            "phone_number": source.phone_number,
            "name": source.name,
            **classification,
            "stage": crm.stage,
            "tags": crm.tags,
            "assigned_to": crm.assigned_to,
            "version": crm.version,
            "read": bool(read and read.last_message_id >= classification.get("last_message_id", 0)),
            "chatwoot_url": settings.businesses[source.business_id].deep_link(
                source.state.get("chatwoot_conversation_id")
            ),
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "whatsapp-crm"}

    @app.get("/ready")
    def ready() -> dict[str, str]:
        try:
            with engine.connect() as conn:
                version = conn.scalar(text("SELECT version_num FROM alembic_version"))
                if version != "0001_crm":
                    raise HTTPException(503, "CRM migrations required")
        except SQLAlchemyError:
            raise HTTPException(503, "CRM database unavailable or migrations required") from None
        return {"status": "ready"}

    @app.get("/v1/capabilities")
    def capabilities(principal: Auth) -> dict[str, Any]:
        return {
            "source_read_only": True,
            "crm_updates": True,
            "manual_handoff_enabled": False,
            "manual_handoff_contract": {
                "version": 1,
                "transport": "authenticated_bot_endpoint_only",
                "implemented": False,
            },
            "send_messages": False,
            "resolve_conversations": False,
            "resume_bot": False,
            "notifications_enabled": settings.notifications_enabled,
        }

    @app.get("/v1/businesses")
    def businesses(principal: Auth) -> list[dict[str, str]]:
        return [{"business_id": name} for name in principal.businesses]

    @app.get("/v1/agents")
    def agents(principal: Auth, session: DB) -> list[dict[str, Any]]:
        return [
            {
                "profile": a.profile,
                "businesses": sorted(set(a.allowed_businesses) & set(principal.businesses)),
            }
            for a in session.scalars(select(Agent))
            if set(a.allowed_businesses) & set(principal.businesses)
        ]

    @app.get("/v1/inbox")
    def inbox(
        principal: Auth,
        session: DB,
        bucket: Bucket = "awaiting_human",
        business_id: Annotated[list[str] | None, Query()] = None,
        search: str = Query(default="", max_length=300),
        priority: int | None = Query(default=None, ge=0, le=4),
        assigned_to: str | None = None,
        tag: str | None = None,
        stage: str | None = None,
        cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        requested = sorted(set(business_id or principal.businesses))
        for business in requested:
            principal.authorize(business)
        scope = hashlib.sha256(
            json.dumps(
                [
                    principal.profile,
                    principal.businesses,
                    requested,
                    bucket,
                    search,
                    priority,
                    assigned_to,
                    tag,
                    stage,
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        query = (
            select(SourceConversation, CRMConversation)
            .join(
                CRMConversation,
                and_(
                    SourceConversation.business_id == CRMConversation.business_id,
                    SourceConversation.phone_number == CRMConversation.phone_number,
                ),
            )
            .where(CRMConversation.business_id.in_(requested))
        )
        if bucket != "all":
            query = query.where(CRMConversation.bucket == bucket)
        if priority is not None:
            query = query.where(CRMConversation.classification["priority"].as_integer() == priority)
        if assigned_to is not None:
            query = query.where(
                CRMConversation.assigned_to.is_(None)
                if assigned_to == "unassigned"
                else CRMConversation.assigned_to == assigned_to
            )
        if stage:
            query = query.where(CRMConversation.stage == stage)
        if tag:
            # JSONB containment isn't portable to JSON; cast only the local PostgreSQL field.
            from sqlalchemy import cast
            from sqlalchemy.dialects.postgresql import JSONB

            query = query.where(cast(CRMConversation.tags, JSONB).contains([tag]))
        if search:
            pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            matching = (
                select(SourceMessage.source_id)
                .where(
                    SourceMessage.business_id == SourceConversation.business_id,
                    SourceMessage.phone_number == SourceConversation.phone_number,
                    SourceMessage.content.ilike(pattern, escape="\\"),
                )
                .exists()
            )
            query = query.where(
                or_(
                    SourceConversation.name.ilike(pattern, escape="\\"),
                    SourceConversation.phone_number.ilike(pattern, escape="\\"),
                    matching,
                )
            )
        if cursor:
            value = decode_cursor(cursor, scope, settings)
            try:
                key = (value["sort_key"], value["business_id"], value["phone_number"])
            except KeyError:
                raise HTTPException(400, "Invalid cursor fields") from None
            query = query.where(
                tuple_(CRMConversation.sort_key, CRMConversation.business_id, CRMConversation.phone_number)
                > key
            )
        rows = session.execute(
            query.order_by(
                CRMConversation.sort_key, CRMConversation.business_id, CRMConversation.phone_number
            ).limit(limit + 1)
        ).all()
        items = [display(session, principal, source, crm) for source, crm in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1][1]
            next_cursor = encode_cursor(
                {
                    "sort_key": last.sort_key,
                    "business_id": last.business_id,
                    "phone_number": last.phone_number,
                },
                scope,
                settings,
            )
        return {"items": items, "next_cursor": next_cursor}

    @app.get("/v1/conversations/{business_id}/{phone_number}")
    def detail(
        business_id: str,
        phone_number: str,
        principal: Auth,
        session: DB,
        limit: int = Query(default=100, ge=1, le=200),
        cursor: str | None = None,
    ) -> dict[str, Any]:
        source, crm = conversation(session, principal, business_id, phone_number)
        scope = f"messages:{principal.profile}:{','.join(principal.businesses)}:{business_id}:{phone_number}"
        query = select(SourceMessage).where(
            SourceMessage.business_id == business_id, SourceMessage.phone_number == phone_number
        )
        if cursor:
            data = decode_cursor(cursor, scope, settings)
            query = query.where(
                tuple_(SourceMessage.created_at, SourceMessage.source_id)
                < (timestamp(data["created_at"]), int(data["id"]))
            )
        rows = session.scalars(
            query.order_by(SourceMessage.created_at.desc(), SourceMessage.source_id.desc()).limit(limit + 1)
        ).all()
        next_cursor = (
            encode_cursor(
                {"created_at": rows[limit - 1].created_at.isoformat(), "id": rows[limit - 1].source_id},
                scope,
                settings,
            )
            if len(rows) > limit
            else None
        )
        return {
            **display(session, principal, source, crm),
            "source_state": source.state,
            "customer": source.customer,
            "handoff_delivery": source.handoff,
            "messages": [
                {
                    "id": m.source_id,
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at,
                    "metadata": m.message_metadata,
                    "event_key": m.event_key,
                }
                for m in reversed(rows[:limit])
            ],
            "next_cursor": next_cursor,
        }

    @app.patch("/v1/conversations/{business_id}/{phone_number}/crm")
    def update_crm(
        business_id: str, phone_number: str, body: CRMUpdate, principal: Auth, session: DB
    ) -> dict[str, Any]:
        source, crm = conversation(session, principal, business_id, phone_number, True)
        if crm.version != body.version:
            raise HTTPException(409, "CRM version changed; reload before updating")
        values = body.model_dump(exclude_unset=True, exclude={"version"})
        if any(values.get(k, "") is None for k in ("stage", "tags")):
            raise HTTPException(422, "Stage and tags cannot be null")
        check_assignment(session, principal, business_id, body.assigned_to)
        for key, value in values.items():
            setattr(crm, key, value)
        crm.version += 1
        audit(session, principal, business_id, phone_number, "crm.update", {"fields": sorted(values)})
        return display(session, principal, source, crm)

    @app.post("/v1/conversations/{business_id}/{phone_number}/read")
    def mark_read(
        business_id: str, phone_number: str, body: ReadInput, principal: Auth, session: DB
    ) -> dict[str, bool]:
        _, crm = conversation(session, principal, business_id, phone_number, True)
        row = session.get(ReadState, (business_id, phone_number, principal.profile))
        if body.read:
            if row is None:
                row = ReadState(business_id=business_id, phone_number=phone_number, profile=principal.profile)
                session.add(row)
            row.read_at, row.last_message_id = now(), crm.classification["last_message_id"]
        elif row:
            session.delete(row)
        audit(session, principal, business_id, phone_number, "crm.read", {"read": body.read})
        return {"read": body.read}

    def note_response(row: Note) -> dict[str, Any]:
        return {"id": row.id, "body": row.body, "profile": row.profile, "updated_at": row.updated_at}

    def task_response(row: Task) -> dict[str, Any]:
        return {
            "id": row.id,
            "title": row.title,
            "profile": row.profile,
            "assigned_to": row.assigned_to,
            "due_at": row.due_at,
            "completed": row.completed,
        }

    @app.get("/v1/conversations/{business_id}/{phone_number}/notes")
    def notes(
        business_id: str,
        phone_number: str,
        principal: Auth,
        session: DB,
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        conversation(session, principal, business_id, phone_number)
        return [
            note_response(n)
            for n in session.scalars(
                select(Note)
                .where(Note.business_id == business_id, Note.phone_number == phone_number)
                .order_by(Note.updated_at.desc(), Note.id)
                .offset(offset)
                .limit(limit)
            )
        ]

    @app.post("/v1/conversations/{business_id}/{phone_number}/notes", status_code=201)
    def add_note(
        business_id: str, phone_number: str, body: NoteInput, principal: Auth, session: DB
    ) -> dict[str, Any]:
        conversation(session, principal, business_id, phone_number)
        row = Note(
            business_id=business_id, phone_number=phone_number, profile=principal.profile, body=body.body
        )
        session.add(row)
        session.flush()
        audit(session, principal, business_id, phone_number, "note.create", {"id": row.id})
        return note_response(row)

    def scoped_note(session: Session, principal: Actor, business: str, phone: str, item: str) -> Note:
        conversation(session, principal, business, phone)
        row = session.get(Note, item)
        if not row or (row.business_id, row.phone_number) != (business, phone):
            raise HTTPException(404, "Note not found")
        return row

    @app.get("/v1/conversations/{business_id}/{phone_number}/notes/{item_id}")
    def get_note(
        business_id: str, phone_number: str, item_id: str, principal: Auth, session: DB
    ) -> dict[str, Any]:
        return note_response(scoped_note(session, principal, business_id, phone_number, item_id))

    @app.patch("/v1/conversations/{business_id}/{phone_number}/notes/{item_id}")
    def edit_note(
        business_id: str, phone_number: str, item_id: str, body: NoteInput, principal: Auth, session: DB
    ) -> dict[str, Any]:
        row = scoped_note(session, principal, business_id, phone_number, item_id)
        row.body, row.updated_at = body.body, now()
        audit(session, principal, business_id, phone_number, "note.update", {"id": row.id})
        return note_response(row)

    @app.delete("/v1/conversations/{business_id}/{phone_number}/notes/{item_id}", status_code=204)
    def delete_note(business_id: str, phone_number: str, item_id: str, principal: Auth, session: DB) -> None:
        row = scoped_note(session, principal, business_id, phone_number, item_id)
        session.delete(row)
        audit(session, principal, business_id, phone_number, "note.delete", {"id": row.id})

    @app.get("/v1/conversations/{business_id}/{phone_number}/tasks")
    def tasks(
        business_id: str,
        phone_number: str,
        principal: Auth,
        session: DB,
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        conversation(session, principal, business_id, phone_number)
        return [
            task_response(t)
            for t in session.scalars(
                select(Task)
                .where(Task.business_id == business_id, Task.phone_number == phone_number)
                .order_by(Task.id)
                .offset(offset)
                .limit(limit)
            )
        ]

    @app.post("/v1/conversations/{business_id}/{phone_number}/tasks", status_code=201)
    def add_task(
        business_id: str, phone_number: str, body: TaskInput, principal: Auth, session: DB
    ) -> dict[str, Any]:
        conversation(session, principal, business_id, phone_number)
        check_assignment(session, principal, business_id, body.assigned_to)
        row = Task(
            business_id=business_id, phone_number=phone_number, profile=principal.profile, **body.model_dump()
        )
        session.add(row)
        session.flush()
        audit(session, principal, business_id, phone_number, "task.create", {"id": row.id})
        return task_response(row)

    def scoped_task(session: Session, principal: Actor, business: str, phone: str, item: str) -> Task:
        conversation(session, principal, business, phone)
        row = session.get(Task, item)
        if not row or (row.business_id, row.phone_number) != (business, phone):
            raise HTTPException(404, "Task not found")
        return row

    @app.get("/v1/conversations/{business_id}/{phone_number}/tasks/{item_id}")
    def get_task(
        business_id: str, phone_number: str, item_id: str, principal: Auth, session: DB
    ) -> dict[str, Any]:
        return task_response(scoped_task(session, principal, business_id, phone_number, item_id))

    @app.patch("/v1/conversations/{business_id}/{phone_number}/tasks/{item_id}")
    def edit_task(
        business_id: str, phone_number: str, item_id: str, body: TaskUpdate, principal: Auth, session: DB
    ) -> dict[str, Any]:
        row = scoped_task(session, principal, business_id, phone_number, item_id)
        check_assignment(session, principal, business_id, body.assigned_to)
        values = body.model_dump(exclude_unset=True)
        if any(values.get(key, "") is None for key in ("title", "completed")):
            raise HTTPException(422, "Title and completed cannot be null")
        for key, value in values.items():
            setattr(row, key, value)
        audit(session, principal, business_id, phone_number, "task.update", {"id": row.id})
        return task_response(row)

    @app.delete("/v1/conversations/{business_id}/{phone_number}/tasks/{item_id}", status_code=204)
    def delete_task(business_id: str, phone_number: str, item_id: str, principal: Auth, session: DB) -> None:
        row = scoped_task(session, principal, business_id, phone_number, item_id)
        session.delete(row)
        audit(session, principal, business_id, phone_number, "task.delete", {"id": row.id})

    @app.get("/v1/pipeline")
    def pipeline(principal: Auth, session: DB) -> dict[str, Any]:
        rows = session.execute(
            select(CRMConversation.business_id, CRMConversation.stage, func.count())
            .where(CRMConversation.business_id.in_(principal.businesses))
            .group_by(CRMConversation.business_id, CRMConversation.stage)
        )
        return {"items": [{"business_id": b, "stage": s, "count": c} for b, s, c in rows]}

    @app.get("/v1/analytics")
    def analytics(principal: Auth, session: DB) -> dict[str, Any]:
        rows = session.execute(
            select(CRMConversation.business_id, CRMConversation.bucket, func.count())
            .where(CRMConversation.business_id.in_(principal.businesses))
            .group_by(CRMConversation.business_id, CRMConversation.bucket)
        )
        return {
            "snapshot_at": now(),
            "items": [{"business_id": b, "bucket": s, "count": c} for b, s, c in rows],
        }

    @app.get("/v1/notifications")
    def notifications(
        principal: Auth, session: DB, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)
    ) -> dict[str, Any]:
        agent = session.get(Agent, principal.profile)
        allowed = [b for b in principal.businesses if agent and preference(agent, b, "in_app")]
        query = (
            select(NotificationEvent)
            .where(NotificationEvent.business_id.in_(allowed))
            .order_by(NotificationEvent.created_at.desc(), NotificationEvent.id)
            .offset(offset)
            .limit(limit)
        )
        return {
            "items": [
                {
                    "id": e.id,
                    "business_id": e.business_id,
                    "phone_number": e.phone_number,
                    "url": e.link,
                    "created_at": e.created_at,
                }
                for e in session.scalars(query)
            ]
        }

    @app.get("/v1/me/preferences")
    def preferences(principal: Auth, session: DB) -> dict[str, Any]:
        row = session.get(Agent, principal.profile)
        return (
            {b: row.preferences.get(b, {"in_app": True, "web_push": True}) for b in principal.businesses}
            if row
            else {}
        )

    @app.patch("/v1/me/preferences")
    def update_preferences(body: PreferencesInput, principal: Auth, session: DB) -> dict[str, bool]:
        principal.authorize(body.business_id)
        row = session.get(Agent, principal.profile)
        assert row is not None
        row.preferences = {
            **row.preferences,
            body.business_id: {"in_app": body.in_app, "web_push": body.web_push},
        }
        audit(session, principal, body.business_id, "", "preferences.update")
        return {"ok": True}

    @app.post("/v1/push/subscriptions", status_code=201)
    def subscribe(body: PushInput, principal: Auth, session: DB) -> dict[str, str]:
        try:
            allowed = endpoint_allowed(body.endpoint, settings)
        except ValueError:
            allowed = False
        if not allowed:
            raise HTTPException(422, "Push endpoint is not an allowed push provider")
        digest = endpoint_hash(body.endpoint)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id("push:" + digest)})
        # Endpoint belongs to one actor, no cross-profile subscription takeover.
        row = session.scalar(
            select(PushSubscription).where(PushSubscription.endpoint_hash == digest).with_for_update()
        )
        if row and row.profile != principal.profile:
            raise HTTPException(409, "Subscription already registered to another profile")
        if row is None:
            row = PushSubscription(profile=principal.profile, endpoint_hash=digest)
            session.add(row)
        elif not row.active:
            row.created_at = now()
        row.subscription, row.active = body.model_dump(), True
        session.flush()
        audit(session, principal, "", "", "push.subscribe", {"id": row.id})
        return {"id": row.id}

    @app.delete("/v1/push/subscriptions/{item_id}", status_code=204)
    def unsubscribe(item_id: str, principal: Auth, session: DB) -> None:
        row = session.get(PushSubscription, item_id)
        if not row or row.profile != principal.profile:
            raise HTTPException(404, "Subscription not found")
        row.active = False
        audit(session, principal, "", "", "push.unsubscribe", {"id": row.id})

    @app.get("/v1/sync-status")
    def sync_status(principal: Auth, session: DB) -> dict[str, Any]:
        items = []
        for business in principal.businesses:
            row = session.get(SyncStatus, business)
            items.append(
                {
                    "business_id": business,
                    "baseline_complete": bool(row and row.baseline_complete),
                    "last_success": row.last_success if row else None,
                    "last_error": row.last_error if row else None,
                    "data_quality_warnings": row.warnings if row else [],
                    "stale": not row
                    or not row.last_success
                    or row.last_success < now() - timedelta(minutes=5),
                }
            )
        return {"items": items}

    @app.post("/internal/sync")
    def run_sync(request: Request) -> dict[str, str]:
        if request.headers.get("origin") or not hmac.compare_digest(
            request.headers.get("x-crm-sync-secret", ""), settings.sync_secret
        ):
            raise HTTPException(401, "Internal authentication required")
        return Synchronizer(settings, sessions).run()

    return app
