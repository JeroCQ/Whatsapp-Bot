"""Private central runtime registry; no browser secrets or alternative sender."""
import hashlib
import hmac
import os
import re
from functools import lru_cache
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

TABLE = "chatty_runtime_businesses"
BUSINESS = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
SERVICE_ROLES = {"commercial-api", "commercial-worker", "central-worker", "pilot-api", "pilot-worker"}


class RegistryError(RuntimeError):
    pass


@lru_cache(maxsize=64)
def runtime_token(business, kind="business"):
    rows = Registry().request("GET", "chatty_runtime_tokens", params={
        "id": "eq." + kind + ":" + business, "limit": "1"})
    if rows:
        return rows[0]["token"]
    secret = os.environ.get("CHATTY_RUNTIME_SECRET", "")
    if len(secret) < 32:
        raise RegistryError("central_runtime_secret_missing")
    return hmac.new(secret.encode(), (kind + ":" + business).encode(), hashlib.sha256).hexdigest()


class Registry:
    def __init__(self):
        self.origin = os.environ.get("SUPABASE_URL", "").rstrip("/")
        self.key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        parsed = urlsplit(self.origin)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.path
                or parsed.username or parsed.password or parsed.query or parsed.fragment or not self.key):
            raise RegistryError("central_supabase_configuration_missing")
        self.headers = {"apikey": self.key, "Authorization": "Bearer " + self.key,
                        "Accept-Profile": "public", "Content-Profile": "public"}

    def request(self, method, path, *, params=None, body=None):
        try:
            with httpx.Client(timeout=httpx.Timeout(30, connect=5), follow_redirects=False) as client:
                response = client.request(method, self.origin + "/rest/v1/" + path,
                                          params=params, json=body,
                                          headers={**self.headers, "Prefer": "return=representation"})
                if response.status_code >= 300:
                    raise RegistryError("central_registry_request_rejected")
                return response.json()
        except (httpx.HTTPError, ValueError):
            raise RegistryError("central_registry_unavailable") from None

    def get(self, business):
        if not BUSINESS.fullmatch(business):
            raise RegistryError("central_business_invalid")
        rows = self.request("GET", TABLE, params={"business_id": "eq." + business, "limit": "1"})
        if not isinstance(rows, list) or len(rows) != 1:
            raise RegistryError("central_business_not_registered")
        return rows[0]

    def all(self):
        rows = self.request("GET", TABLE, params={"order": "business_id.asc"})
        if not isinstance(rows, list):
            raise RegistryError("central_registry_response_invalid")
        return rows

    def update(self, row, values):
        result = self.request("PATCH", TABLE,
                              params={"business_id": "eq." + row["business_id"],
                                      "version": "eq." + str(row["version"])},
                              body={**values, "version": row["version"] + 1})
        if not result:
            raise RegistryError("central_registry_version_conflict")
        return result[0]

    def response(self, row):
        env = {}
        source = row["source_configuration"]
        if row["mode"] == "central":
            if not source or row.get("migration", {}).get("status") != "ready":
                raise RegistryError("central_migration_not_ready")
            env = {**source, "BUSINESS_ID": row["business_id"],
                   "SUPABASE_URL": self.origin, "SUPABASE_SERVICE_ROLE_KEY": self.key,
                   "SUPABASE_SCHEMA": "public", "SUPABASE_TABLE_PREFIX": row["target_prefix"]}
            for old, new in row.get("migration", {}).get("public_url_map", {}).items():
                if "PRESAVED_FILES_JSON" in env:
                    env["PRESAVED_FILES_JSON"] = env["PRESAVED_FILES_JSON"].replace(old, new)
        return {"business_id": row["business_id"], "channel_id": row["channel_id"],
                "version": row["version"], "mode": row["mode"], "environment": env}

    def service(self, role):
        if role not in SERVICE_ROLES:
            raise RegistryError("central_service_unknown")
        rows = self.request("GET", "chatty_runtime_services", params={"role": "eq." + role})
        if len(rows) != 1:
            raise RegistryError("central_service_not_registered")
        return rows[0]

    def service_update(self, role, values):
        self.service(role)
        rows = self.request("PATCH", "chatty_runtime_services", params={"role": "eq." + role}, body=values)
        return rows[0]

    def service_response(self, row):
        env = {}
        if row["mode"] == "central":
            source_role = ("commercial-worker" if row["role"] in {"central-worker", "pilot-worker"}
                           else "commercial-api" if row["role"] == "pilot-api" else row["role"])
            source = self.service(source_role)["source_configuration"]
            database_url = os.environ.get("CHATTY_CENTRAL_DATABASE_URL", "")
            if not source or not database_url.startswith("postgresql+psycopg://"):
                raise RegistryError("central_crm_destination_not_prepared")
            businesses = ",".join(item["business_id"] for item in self.all())
            pilot = self.service("pilot-api")["source_configuration"]
            media_enabled = any(item.get("CRM_MEDIA_ENABLED", "").lower() == "true" for item in (source, pilot))
            env = {**source, "CRM_DATABASE_URL": database_url,
                   "CRM_DATABASE_SCHEMA": "chatty_crm",
                   "SUPABASE_URL": self.origin, "SUPABASE_SERVICE_ROLE_KEY": self.key,
                   "CHATTY_CENTRAL_ENABLED": "true", "CRM_BUSINESSES": businesses,
                   "CRM_SOURCE_MODE": "bot_api", "CRM_USAGE_COLLECTION_ENABLED": "true",
                   "CRM_MEDIA_ENABLED": "true" if media_enabled else "false",
                   "CRM_MEDIA_ALLOWED_BUSINESSES": businesses}
        return {"role": row["role"], "mode": row["mode"], "frozen": row["frozen"],
                "epoch": row["epoch"], "environment": env,
                "migration_target_url": os.environ.get("CHATTY_CENTRAL_DATABASE_URL", "")}


class Bootstrap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configuration: dict[str, str]
    commit: str = Field(default="", max_length=40)
    bot_api_url: str


class ServiceBootstrap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configuration: dict[str, str]
    service_api_url: str = ""


class ServiceState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frozen: bool | None = None
    epoch: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{16,80}$")
    paused_epoch: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{16,80}$")


def create_runtime_router(settings, sessions):
    router = APIRouter()

    def service_auth(role, request):
        if (role not in SERVICE_ROLES
                or request.headers.get("origin") or request.headers.get("sec-fetch-site")
                or not hmac.compare_digest(request.headers.get("x-chatty-runtime-token", ""),
                                           runtime_token(role, "service"))):
            raise HTTPException(401, "central_service_authentication_required")

    @router.post("/internal/runtime/services/{role}/bootstrap")
    def service_bootstrap(role: str, body: ServiceBootstrap, request: Request):
        service_auth(role, request)
        from crm_service.runtime_service import _TRANSFER
        if any(key not in _TRANSFER for key in body.configuration):
            raise HTTPException(422, "central_service_configuration_out_of_scope")
        registry = Registry()
        row = registry.service(role)
        if role != "central-worker" and row["mode"] == "legacy":
            values = {"source_configuration": body.configuration}
            if body.service_api_url:
                parsed = urlsplit(body.service_api_url)
                if (not role.endswith("-api") or parsed.scheme != "https" or not parsed.hostname
                        or parsed.path or parsed.username or parsed.password or parsed.query or parsed.fragment
                        or (row.get("api_url") and row["api_url"] != body.service_api_url)):
                    raise HTTPException(409, "central_service_origin_mismatch")
                values["api_url"] = body.service_api_url
            row = registry.service_update(role, values)
        return registry.service_response(row)

    @router.get("/internal/runtime/services/{role}/state")
    def service_state(role: str, request: Request):
        service_auth(role, request)
        row = Registry().service(role)
        return {key: row[key] for key in ("frozen", "epoch", "paused_epoch")}

    @router.post("/internal/runtime/services/{role}/state")
    def service_state_update(role: str, body: ServiceState, request: Request):
        service_auth(role, request)
        registry, values = Registry(), body.model_dump(exclude_none=True)
        row = registry.service(role)
        requested = values.get("epoch", values.get("paused_epoch"))
        if row["epoch"] and row["epoch"] != requested:
            raise HTTPException(409, "central_service_epoch_conflict")
        if "paused_epoch" in values and (not row["frozen"] or row["epoch"] != requested):
            raise HTTPException(409, "central_service_is_not_frozen")
        if values.get("frozen") is False:
            values.update({"epoch": "", "paused_epoch": ""})
        row = registry.service_update(role, values)
        return {key: row[key] for key in ("frozen", "epoch", "paused_epoch")}

    @router.post("/internal/runtime/businesses/{business}/bootstrap")
    async def bootstrap(business: str, body: Bootstrap, request: Request):
        if (not BUSINESS.fullmatch(business) or request.headers.get("origin")
                or request.headers.get("sec-fetch-site") or not hmac.compare_digest(
                    request.headers.get("x-chatty-runtime-token", ""), runtime_token(business))):
            raise HTTPException(401, "central_runtime_authentication_required")
        if len(await request.body()) > 262144:
            raise HTTPException(413, "central_runtime_configuration_too_large")
        try:
            registry = Registry()
            row = registry.get(business)
            env = body.configuration
            if (env.get("BUSINESS_ID") != business or body.bot_api_url.rstrip("/") != row["bot_api_url"]
                    or not env.get("WA_PHONE_NUMBER_ID") or not env.get("SUPABASE_URL")
                    or not (env.get("SUPABASE_SERVICE_ROLE_KEY") or env.get("SUPABASE_KEY"))):
                raise HTTPException(409, "central_runtime_registration_scope_mismatch")
            if row.get("channel_id") and row["channel_id"] != env["WA_PHONE_NUMBER_ID"]:
                raise HTTPException(409, "central_runtime_channel_change_forbidden")
            if row["mode"] == "legacy" and row["source_configuration"] != env:
                try:
                    row = registry.update(row, {"source_configuration": env,
                                               "channel_id": env["WA_PHONE_NUMBER_ID"],
                                               "source_commit": body.commit})
                except RegistryError as error:
                    if str(error) != "central_registry_version_conflict":
                        raise
                    # Web and RQ can bootstrap simultaneously; accept only the
                    # identical registration, never overwrite a concurrent edit.
                    row = registry.get(business)
                    if row["source_configuration"] != env:
                        raise RegistryError("central_registry_version_conflict") from None
            return registry.response(row)
        except RegistryError as error:
            raise HTTPException(503, str(error)) from None

    class UsageEnvelope(BaseModel):
        model_config = ConfigDict(extra="forbid")
        runtime_version: int = Field(ge=1)
        channel_id: str
        record: dict

    @router.post("/internal/runtime/businesses/{business}/usage/{kind}")
    def usage(business: str, kind: str, body: UsageEnvelope, request: Request):
        from crm_service.central_schemas import AttemptInput, TurnInput
        from crm_service.usage_store import record_attempt, record_turn
        if request.headers.get("origin") or request.headers.get("sec-fetch-site"):
            raise HTTPException(403, "Server-to-server only")
        try:
            row = Registry().get(business)
        except RegistryError as error:
            raise HTTPException(503, str(error)) from None
        secret = row["source_configuration"].get("CRM_BOT_CONTROL_SECRET", "")
        if len(secret) < 32 or not hmac.compare_digest(request.headers.get("x-crm-bot-secret", ""), secret):
            raise HTTPException(401, "Bot control authentication required")
        if (not settings.usage_collection_enabled or kind not in {"attempt", "turn"}
                or body.channel_id != row["channel_id"] or body.record.get("business_id") != business
                or body.runtime_version > row["version"]):
            raise HTTPException(409, "central_usage_scope_or_version_invalid")
        payload = (AttemptInput if kind == "attempt" else TurnInput).model_validate(body.record)
        with sessions.begin() as session:
            return (record_attempt if kind == "attempt" else record_turn)(session, payload)

    return router
