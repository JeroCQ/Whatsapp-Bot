"""Standby-safe isolated pilot factory. Never imports a bot/queue/worker launcher."""
import hashlib
import os
import re
from dataclasses import replace
from collections.abc import Awaitable, Callable
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import make_url
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from crm_service.api import create_app as crm_app
from crm_service.models import PilotPublication, PilotSwitch
from crm_service.pilot import BUSINESS, PHONE, SCHEMA, create_router, validate_operators
from crm_service.security import authenticate
from crm_service.settings import Settings, flag


def isolated_database_url() -> str:
    value = os.environ.get("CHATTY_PILOT_DATABASE_URL", "")
    url = make_url(value)
    identity = hashlib.sha256(f"{url.host}:{url.port or 5432}:{url.database}".encode()).hexdigest()
    commercial = os.environ.get("CHATTY_PILOT_COMMERCIAL_DATABASE_ID", "")
    # Require an explicitly reviewed exclusion, not the word 'staging' or a separate password.
    if (url.drivername != "postgresql+psycopg" or not (url.database or "").startswith("chatty_pilot_")
            or not re.fullmatch(r"[a-f0-9]{64}", commercial) or identity == commercial):
        raise ValueError("pilot_database_isolation_not_confirmed")
    legacy = os.environ.get("CRM_DATABASE_URL")
    if legacy:
        other = make_url(legacy)
        if (url.host, url.port or 5432, url.database) == (other.host, other.port or 5432, other.database):
            raise ValueError("pilot_cannot_use_commercial_crm_database")
    return value


def pilot_settings(service: str = "api") -> Settings:
    settings = Settings.from_env(service, database_url=isolated_database_url())
    if set(settings.businesses) != {BUSINESS} or settings.bot_test_mode:
        raise ValueError("pilot_requires_only_briosos_and_verified_https_bot")
    # Never replay production notifications; existing baseline/cursor machinery is retained.
    return replace(settings, notifications_enabled=False, source_mode="bot_api")


def create_app() -> FastAPI:
    settings = pilot_settings()
    app = crm_app(settings)
    sessions = app.state.sessions
    app.include_router(create_router(settings, sessions))
    app.router.routes[:] = [route for route in app.router.routes if getattr(route, "path", None) != "/ready"]

    @app.get("/ready")
    def ready() -> dict[str, str]:
        try:
            with app.state.engine.connect() as connection:
                if connection.scalar(text("SELECT version_num FROM alembic_version")) != SCHEMA:
                    raise HTTPException(503, "pilot_migration_required")
        except SQLAlchemyError:
            raise HTTPException(503, "pilot_database_unavailable_or_migration_required") from None
        return {"status": "ready", "mode": "standby" if flag("CHATTY_PILOT_STANDBY", True) else "gated"}

    def guard(request: Request) -> None:
        path = unquote(request.url.path)
        if (path in {"/health", "/ready"} or path.startswith("/v1/admin/")
                or path.startswith("/internal/pilot/") or path.startswith("/internal/runtime/")):
            return  # Each Admin/internal route has its own existing JWT/server-secret validation.
        if path.startswith("/internal/"):
            raise HTTPException(403, "pilot_internal_job_submission_forbidden")
        principal = authenticate(request, settings)
        principal.authorize(BUSINESS)
        if request.method == "GET" and path in {"/v1/businesses", "/v1/capabilities"}:
            return  # Existing login can register its existing membership before publication.
        recovery_read = request.method == "GET" and re.fullmatch(
            rf"/v1/conversations/{BUSINESS}/{PHONE}/(?:control|commands/[A-Za-z0-9_.:@+-]+)", path) is not None
        if flag("CHATTY_PILOT_STANDBY", True) and not recovery_read:
            raise HTTPException(409, "pilot_standby_no_operational_requests")
        with sessions.begin() as session:
            state = session.get(PilotSwitch, BUSINESS)
            publication = session.get(PilotPublication, state.publication_version) if state else None
            if (not state or not publication or not publication.activated_at
                    or (state.mode != "shared" and not recovery_read)):
                raise HTTPException(409, "pilot_not_active")
            validate_operators(session, publication.snapshot)
            profiles = {item["profile"] for item in publication.snapshot["operators"]}
            if principal.profile not in profiles:
                raise HTTPException(403, "operator_not_assigned_to_pilot")
        if path.startswith("/v1/conversations/"):
            pieces = path.split("/")
            if len(pieces) < 5 or pieces[3:5] != [BUSINESS, PHONE]:
                raise HTTPException(403, "pilot_conversation_scope_rejected")
        businesses = request.query_params.getlist("business_id")
        if businesses and businesses != [BUSINESS]:
            raise HTTPException(403, "pilot_business_scope_rejected")

    @app.middleware("http")
    async def scope(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        try:
            await run_in_threadpool(guard, request)
        except HTTPException as error:
            return JSONResponse({"detail": error.detail}, status_code=error.status_code)
        return await call_next(request)

    return app
