"""Drain the existing queue; keep verified ingress durable during the final copy."""
import hashlib
import json
import hmac
import os
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse

FROZEN = "chatty:central:migration:frozen"
ACTIVE = "chatty:central:migration:requests"


def connection():
    from queue_client import get_queue
    queue = get_queue()
    if queue is None:
        raise HTTPException(503, "migration_requires_existing_queue")
    return queue.connection


def create_router(config):
    router = APIRouter()

    def authorize(request):
        token = os.environ.get("CHATTY_RUNTIME_TOKEN", "")
        if (len(token) < 32 or request.headers.get("origin") or request.headers.get("sec-fetch-site")
                or not hmac.compare_digest(request.headers.get("x-chatty-runtime-token", ""), token)):
            raise HTTPException(401, "central_migration_authentication_required")

    class Epoch(BaseModel):
        epoch: str = Field(pattern=r"^[A-Za-z0-9_-]{16,80}$")
        hold: bool = False

    def status():
        from central_runtime_client import metadata
        from observability import deployment_sha
        from rq import Worker
        redis = connection()
        workers = Worker.all(connection=redis)
        busy = [worker.name for worker in workers if worker.get_current_job_id()]
        bindings = []
        for worker in workers:
            try:
                value = json.loads(redis.hget(worker.key, "chatty_runtime") or b"{}")
            except (ValueError, TypeError):
                value = {}
            bindings.append({**(value if isinstance(value, dict) else {}), "name": worker.name})
        return {**metadata(), "business_id": config.BUSINESS_ID,
                "channel_id": str(config.WA_PHONE_NUMBER_ID),
                "commit": deployment_sha(), "schema": config.SUPABASE_SCHEMA,
                "table_prefix": config.SUPABASE_TABLE_PREFIX,
                "storage_origin": config.SUPABASE_URL,
                "frozen": bool(redis.get(FROZEN)), "workers_busy": len(busy),
                "workers": bindings,
                "epoch": (redis.get(FROZEN) or b"").decode(),
                "requests_active": redis.zcard(ACTIVE)}

    @router.get("/internal/runtime/migration/status")
    def read(request: Request):
        authorize(request)
        return status()

    @router.post("/internal/runtime/migration/freeze")
    def freeze(body: Epoch, request: Request):
        authorize(request)
        from rq.suspension import suspend
        redis = connection()
        current = redis.get(FROZEN)
        if current and current.decode() != body.epoch:
            raise HTTPException(409, "another_central_migration_holds_the_queue")
        # Switch to a durable hold before activating copied storage: expiration
        # after activation must never resume an old writer against an old DB.
        redis.set(FROZEN, body.epoch, ex=None if body.hold else 900)
        suspend(redis, ttl=None if body.hold else 900)
        return status()

    @router.post("/internal/runtime/migration/resume")
    def resume(body: Epoch, request: Request):
        authorize(request)
        from rq.suspension import resume as rq_resume
        redis = connection()
        current = redis.get(FROZEN)
        if not current or current.decode() != body.epoch:
            raise HTTPException(409, "central_migration_epoch_mismatch")
        redis.delete(FROZEN)
        rq_resume(redis)
        return status()

    return router


def install_request_tracking(app):
    if not os.environ.get("CHATTY_RUNTIME_URL"):
        return

    @app.middleware("http")
    async def track(request, call_next):
        if request.method in {"GET", "HEAD", "OPTIONS"} or request.url.path.startswith("/internal/runtime/"):
            return await call_next(request)
        redis, member = connection(), uuid.uuid4().hex
        if redis.get(FROZEN) and request.url.path not in {"/", "/webhook"}:
            # Management writes must drain too; signed Meta ingress keeps its
            # existing durable queue/replay path and is never acknowledged early.
            return JSONResponse({"detail": "central_migration_in_progress"}, status_code=503)
        # Frozen Meta ingress is buffered by receive_webhook, not written to the
        # old SQL source. Count every request until its handler has finished.
        redis.zadd(ACTIVE, {member: time.time()})
        redis.expire(ACTIVE, 1200)
        try:
            return await call_next(request)
        finally:
            redis.zrem(ACTIVE, member)


def buffer_verified_webhook(raw, signature):
    from queue_client import enqueue
    digest = hashlib.sha256(raw).hexdigest()
    enqueue(replay_verified_webhook, raw, signature, job_id="central_ingress_" + digest)


def replay_verified_webhook(raw, signature):
    import asyncio
    from fastapi import BackgroundTasks
    from starlette.requests import Request
    from main import receive_webhook
    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    request = Request({"type": "http", "method": "POST", "path": "/webhook",
                       "headers": [(b"x-hub-signature-256", signature.encode()),
                                   (b"content-type", b"application/json")]}, receive=receive)
    # The existing signature, destination and event/command dedupe run again.
    asyncio.run(receive_webhook(request, BackgroundTasks()))
