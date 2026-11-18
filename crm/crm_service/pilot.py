"""Explicit Briosos publication. Mounted ONLY by the isolated pilot entrypoint."""
import hmac
import os
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from crm_service import usage_store
from crm_service.bot_gateway import BotGateway, BotGatewayError
from crm_service.central_config import preview, public_business
from crm_service.central_schemas import AttemptInput, TurnInput
from crm_service.models import Agent, AuditEvent, BusinessDraft, PilotPublication, PilotSwitch, now
from crm_service.schemas import Input
from crm_service.security import Actor, authenticate
from crm_service.settings import Settings, flag

BUSINESS = "briosos"
PHONE = "573105338124"
SCHEMA = "0006_scoped_pilot"


class Publish(Input):
    draft_version: int = Field(strict=True, ge=1)


class Switch(Input):
    revision: int = Field(strict=True, ge=0)
    publication_version: int = Field(strict=True, ge=1)


class UsageEnvelope(Input):
    publication_version: int = Field(strict=True, ge=1)
    channel_id: str = Field(pattern=r"^[0-9]{1,32}$")
    record: AttemptInput | TurnInput


def lock(session: Session) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(681735552019)"))


def public_state(session: Session) -> dict[str, Any]:
    state = session.get(PilotSwitch, BUSINESS)
    publications = session.scalars(select(PilotPublication).order_by(PilotPublication.version.desc()).limit(20))
    return {"business_id": BUSINESS, "test_number": "+" + PHONE,
            "mode": state.mode if state else "standby", "revision": state.revision if state else 0,
            "publication_version": state.publication_version if state else None,
            "activation_enabled": flag("CHATTY_PILOT_ACTIVATION_ENABLED") and not flag("CHATTY_PILOT_STANDBY", True),
            "publications": [{"version": row.version, "draft_version": row.snapshot["draft_version"],
                "created_at": row.created_at.isoformat(), "activated": row.activated_at is not None}
                for row in publications]}


def validate_operators(session: Session, snapshot: dict[str, Any]) -> None:
    """Recheck current memberships; publication does not grant or widen access."""
    profiles = snapshot["operators"]
    if not profiles:
        raise HTTPException(409, "pilot_requires_existing_authorized_operator")
    for item in profiles:
        agent = session.get(Agent, item["profile"])
        current = agent.preferences.get("_crm_operator", {}) if agent else {}
        if (not agent or BUSINESS not in agent.allowed_businesses or current.get("role") == "retired"
                or current.get("queue") != item.get("queue") or current.get("aliases", []) != item["aliases"]):
            raise HTTPException(409, "pilot_operator_membership_changed_republish")


def live_status(settings: Settings) -> dict[str, Any]:
    gateway = None
    try:
        gateway = BotGateway(settings.businesses[BUSINESS], timeout=5)
        status = gateway._request("GET", f"/internal/crm/v1/businesses/{BUSINESS}/pilot-status")
    except BotGatewayError:
        raise HTTPException(503, "pilot_bot_preflight_unavailable") from None
    finally:
        if gateway is not None:
            gateway.close()
    if (status.get("business_id") != BUSINESS or status.get("protocol") != 1
            or status.get("test_phone") != PHONE or not status.get("channel_id")
            or not status.get("storage_fingerprint") or not status.get("source_phone_filter")):
        raise HTTPException(409, "pilot_bot_scope_or_code_incompatible")
    return status


def create_router(settings: Settings, sessions: sessionmaker[Session]) -> APIRouter:
    router = APIRouter()

    def admin(request: Request, session: Session) -> Actor:
        principal = authenticate(request, settings)
        if not principal.is_admin:
            raise HTTPException(403, "Admin access required")
        # Same authenticated agent registry; no user/session system or membership widening.
        if session.get(Agent, principal.profile) is None:
            session.add(Agent(profile=principal.profile, allowed_businesses=list(principal.businesses),
                              preferences={"_crm_operator": {"role": "admin"}}))
            session.flush()
        return principal

    def server(request: Request) -> None:
        secret = settings.businesses[BUSINESS].bot_control_secret
        if (request.headers.get("origin") or request.headers.get("sec-fetch-site") or len(secret) < 32
                or not hmac.compare_digest(request.headers.get("x-crm-bot-secret", ""), secret)):
            raise HTTPException(401, "pilot_server_auth_required")

    def audit(session: Session, principal: Actor, action: str, version: int) -> None:
        session.add(AuditEvent(business_id=BUSINESS, phone_number=PHONE, profile=principal.profile,
                              action=action, details={"publication_version": version}))

    @router.get("/v1/admin/pilot")
    def state(request: Request) -> dict[str, Any]:
        with sessions.begin() as session:
            admin(request, session)
            result = public_state(session)
        try:
            result["usage_health"] = {"available": True, "collectors": live_status(settings).get("usage", [])}
        except HTTPException:
            result["usage_health"] = {"available": False, "reason": "pilot_bot_preflight_unavailable"}
        result["usage_health"]["complete_accounting"] = False
        return result

    @router.post("/v1/admin/pilot/publications")
    def publish(body: Publish, request: Request) -> dict[str, Any]:
        with sessions.begin() as session:
            principal = admin(request, session)
            lock(session)
            draft = session.get(BusinessDraft, BUSINESS)
            if not draft or draft.version != body.draft_version:
                raise HTTPException(409, "draft_version_conflict")
            candidate = preview(session, settings, BUSINESS)
            required = {"bot_control", "source_read"}
            if not required <= candidate.connection_references.keys():
                raise HTTPException(409, "pilot_connections_not_associated")
            if any(candidate.connection_references[purpose] != f"legacy_settings:{BUSINESS}:{purpose}" for purpose in required):
                raise HTTPException(409, "pilot_connection_reference_mismatch")
            status = live_status(settings)  # Runtime preflight only when an Admin explicitly publishes.
            if draft.whatsapp_phone_number_id != status["channel_id"]:
                raise HTTPException(409, "pilot_verified_channel_mismatch")
            members = public_business(session, settings, BUSINESS)["memberships"]
            snapshot = {"draft_version": draft.version, "configuration": candidate.effective,
                        "connections": candidate.connection_references, "channel_id": status["channel_id"],
                        "storage_fingerprint": status["storage_fingerprint"],
                        "bot_commit": status.get("commit", "unknown"),
                        "operators": [member for member in members if member["profile"] in draft.operator_profiles]}
            validate_operators(session, snapshot)
            row = PilotPublication(business_id=BUSINESS, phone_number=PHONE, snapshot=snapshot, created_by=principal.profile)
            session.add(row)
            session.flush()
            audit(session, principal, "pilot.configuration_published_inactive", row.version)
            return {"version": row.version, "active": False}

    @router.post("/v1/admin/pilot/{action}")
    def switch(action: Literal["activate", "disable"], body: Switch, request: Request) -> dict[str, Any]:
        with sessions.begin() as session:
            principal = admin(request, session)
            lock(session)
            state = session.get(PilotSwitch, BUSINESS)
            if body.revision != (state.revision if state else 0):
                raise HTTPException(409, "pilot_revision_conflict")
            publication = session.get(PilotPublication, body.publication_version)
            if not publication:
                raise HTTPException(404, "pilot_publication_not_found")
            if action == "activate":
                if not flag("CHATTY_PILOT_ACTIVATION_ENABLED") or flag("CHATTY_PILOT_STANDBY", True):
                    raise HTTPException(409, "pilot_activation_disabled")
                status = live_status(settings)
                expected = os.environ.get("CHATTY_PILOT_EXPECTED_BOT_COMMIT", "")
                if (len(expected) != 40 or status.get("commit") != expected
                        or not status.get("bridge_enabled")
                        or not status.get("source_export_enabled")
                        or status["channel_id"] != publication.snapshot["channel_id"]
                        or status["storage_fingerprint"] != publication.snapshot["storage_fingerprint"]):
                    raise HTTPException(409, "pilot_bot_not_ready_or_storage_changed")
                validate_operators(session, publication.snapshot)
                if not publication.snapshot["configuration"].get("operations_enabled"):
                    raise HTTPException(409, "pilot_operations_not_enabled_in_publication")
                publication.activated_at = publication.activated_at or now()
            elif not state or state.publication_version != publication.version:
                raise HTTPException(409, "pilot_disable_version_mismatch")
            if state is None:
                state = PilotSwitch(business_id=BUSINESS, revision=0)
                session.add(state)
            state.revision += 1
            state.publication_version = publication.version
            state.mode = "shared" if action == "activate" else "legacy"
            audit(session, principal, f"pilot.{action}", publication.version)
            session.flush()
            return public_state(session)

    @router.get("/internal/pilot/config")
    def configuration(request: Request, channel_id: str, phone_number: str) -> dict[str, Any]:
        server(request)
        if phone_number != PHONE:
            raise HTTPException(403, "pilot_scope_rejected")
        with sessions.begin() as session:
            state = session.get(PilotSwitch, BUSINESS)
            publication = session.get(PilotPublication, state.publication_version) if state else None
            if not state or not publication or state.mode == "standby":
                raise HTTPException(409, "pilot_not_activated")
            if channel_id != publication.snapshot["channel_id"]:
                raise HTTPException(403, "pilot_channel_rejected")
            validate_operators(session, publication.snapshot)
            return {"business_id": BUSINESS, "phone_number": PHONE, "mode": state.mode,
                    "revision": state.revision, "publication_version": publication.version, **publication.snapshot}

    @router.post("/internal/pilot/usage/{kind}")
    def usage(kind: Literal["attempt", "turn"], body: UsageEnvelope, request: Request) -> dict[str, Any]:
        server(request)
        record = body.record
        if record.business_id != BUSINESS or record.conversation_id != PHONE:
            raise HTTPException(403, "pilot_usage_scope_rejected")
        if not settings.usage_collection_enabled:
            raise HTTPException(409, "usage_collection_disabled")
        with sessions.begin() as session:
            publication = session.get(PilotPublication, body.publication_version)
            # Accept late telemetry from already activated versions after disable; never replay sends.
            if not publication or not publication.activated_at or body.channel_id != publication.snapshot["channel_id"]:
                raise HTTPException(403, "pilot_usage_publication_rejected")
            if kind == "attempt" and isinstance(record, AttemptInput):
                return usage_store.record_attempt(session, record)
            if kind == "turn" and isinstance(record, TurnInput):
                return usage_store.record_turn(session, record)
            raise HTTPException(422, "pilot_usage_kind_mismatch")

    return router
