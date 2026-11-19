"""Migration bridge for existing CRM services, preserving their JWT and login."""
import hmac
import os
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

STATE = {"frozen": False, "epoch": "", "inflight": 0, "mode": "legacy"}
_TRANSFER = {"CRM_DATABASE_URL", "NOTIFICATIONS_ENABLED", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT",
             "CRM_MEDIA_ENABLED", "CRM_MEDIA_ALLOWED_BUSINESSES", "CRM_SOURCE_MODE",
             "CRM_USAGE_COLLECTION_ENABLED", "CRM_BOT_TEST_MODE", "CRM_BOT_TIMEOUT_SECONDS",
             "SYNC_INTERVAL_SECONDS", "SYNC_PAGE_SIZE", "SYNC_MAX_PAGES", "SYNC_MAX_SECONDS",
             "SYNC_MAX_REQUESTS", "SYNC_MAX_CONNECTIONS", "SYNC_RECONCILE_INTERVAL_SECONDS",
             "SYNC_BACKOFF_BASE_SECONDS", "SYNC_BACKOFF_MAX_SECONDS", "SYNC_MAX_PROJECTIONS",
             "SYNC_MAX_HISTORY_MESSAGES"}


def configured():
    return bool(os.environ.get("CHATTY_RUNTIME_SERVICE_ROLE"))


def remote(method, suffix="", body=None):
    role = os.environ.get("CHATTY_RUNTIME_SERVICE_ROLE", "")
    origin = os.environ.get("CHATTY_RUNTIME_URL", "").rstrip("/")
    token = os.environ.get("CHATTY_RUNTIME_TOKEN", "")
    url = urlsplit(origin)
    if (url.scheme != "https" or not url.hostname or url.path or url.username or url.password
            or url.query or url.fragment or len(token) < 32):
        raise RuntimeError("central_crm_bootstrap_configuration_invalid")
    try:
        with httpx.Client(timeout=httpx.Timeout(20, connect=5), follow_redirects=False) as client:
            response = client.request(method, origin + f"/internal/runtime/services/{role}" + suffix,
                                      json=body, headers={"X-Chatty-Runtime-Token": token})
        if response.status_code != 200:
            raise RuntimeError("central_crm_bootstrap_rejected")
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise RuntimeError("central_crm_bootstrap_unavailable") from None


def bootstrap():
    if not configured():
        return
    snapshot = {key: value for key, value in os.environ.items() if key in _TRANSFER}
    data = remote("POST", "/bootstrap", {"configuration": snapshot})
    if data.get("mode") not in {"legacy", "central"}:
        raise RuntimeError("central_crm_bootstrap_mode_invalid")
    overrides = data.get("environment", {})
    permitted = _TRANSFER | {"SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
                             "CHATTY_CENTRAL_ENABLED", "CRM_BUSINESSES"}
    if (not isinstance(overrides, dict) or any(key not in permitted or not isinstance(value, str)
                                              for key, value in overrides.items())):
        raise RuntimeError("central_crm_bootstrap_response_invalid")
    # Login/signing credentials and the existing consumer's source URL are never
    # replaced. Only the explicitly activated destination is returned by server.
    if data["mode"] == "central":
        os.environ.update(overrides)
    STATE.update({"mode": data["mode"], "frozen": data.get("frozen") is True,
                  "epoch": data.get("epoch", "")})


def install(app):
    if not configured():
        return
    router = APIRouter()

    def authorize(request):
        secret = os.environ.get("CHATTY_RUNTIME_TOKEN", "")
        if (request.headers.get("origin") or request.headers.get("sec-fetch-site")
                or not hmac.compare_digest(request.headers.get("x-chatty-runtime-token", ""), secret)):
            raise HTTPException(401, "central_service_authentication_required")

    class Epoch(BaseModel):
        epoch: str = Field(pattern=r"^[A-Za-z0-9_-]{16,80}$")

    @router.get("/internal/runtime/service/status")
    def status(request: Request):
        authorize(request)
        return {**STATE, "role": os.environ["CHATTY_RUNTIME_SERVICE_ROLE"]}

    @router.post("/internal/runtime/service/freeze")
    def freeze(body: Epoch, request: Request):
        authorize(request)
        if STATE["epoch"] and STATE["epoch"] != body.epoch:
            raise HTTPException(409, "central_service_epoch_conflict")
        remote("POST", "/state", {"frozen": True, "epoch": body.epoch})
        STATE.update({"frozen": True, "epoch": body.epoch})
        return {**STATE}

    @router.post("/internal/runtime/service/resume")
    def resume(body: Epoch, request: Request):
        authorize(request)
        if STATE["epoch"] != body.epoch:
            raise HTTPException(409, "central_service_epoch_conflict")
        remote("POST", "/state", {"frozen": False, "epoch": body.epoch})
        STATE.update({"frozen": False, "epoch": ""})
        return {**STATE}

    app.include_router(router)

    @app.middleware("http")
    async def gate(request, call_next):
        if request.url.path in {"/health", "/ready"} or request.url.path.startswith("/internal/runtime/"):
            return await call_next(request)
        if STATE["frozen"]:
            return JSONResponse({"detail": "central_migration_in_progress"}, status_code=503)
        STATE["inflight"] += 1
        try:
            return await call_next(request)
        finally:
            STATE["inflight"] -= 1


def worker_pause_state():
    if not configured():
        return None
    return remote("GET", "/state")


def worker_acknowledge(epoch):
    remote("POST", "/state", {"paused_epoch": epoch})
