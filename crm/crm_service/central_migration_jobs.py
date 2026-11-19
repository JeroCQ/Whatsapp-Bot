"""Explicit authenticated migration operations; never a message sender or test loop."""
import hmac
import os
import threading
from contextlib import contextmanager
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from crm_service.central_copy import (
    BOT_DATASETS, CRM_DATASETS, DATASETS, CopyError, check_bot_source,
    install, prepare, prepare_staging, run_row, save_progress,
)
from crm_service.central_copy_connection import connect
from crm_service.runtime_registry import Registry, runtime_token

COPY_LOCK = threading.Lock()
API_ROLES = ("commercial-api", "pilot-api")
WORKER_ROLES = ("commercial-worker", "pilot-worker")


class Operation(BaseModel):
    epoch: str = Field(pattern=r"^[A-Za-z0-9_-]{16,80}$")
    action: Literal["prepare", "freeze", "verify-barrier", "final-copy", "install", "abort", "status"]
    dataset: Literal["briosos", "memos", "velvet", "commercial-crm", "pilot-crm"] | None = None


def call(origin, token, path, body=None):
    if not origin.startswith("https://"):
        raise CopyError("Falta el origen HTTPS verificado de un servicio.")
    with httpx.Client(timeout=httpx.Timeout(300, connect=5), follow_redirects=False) as client:
        response = client.request("POST" if body is not None else "GET", origin + path,
                                  json=body, headers={"X-Chatty-Runtime-Token": token})
    if response.status_code != 200:
        raise CopyError(f"Un origen rechazó la operación de migración (HTTP {response.status_code}).")
    return response.json()


@contextmanager
def source_environment(row):
    source = row["source_configuration"]
    values = {
        "CHATTY_MIGRATION_SOURCE_DATABASE_URL": row["migration"].get("source_database_url", ""),
        "CHATTY_MIGRATION_SOURCE_STORAGE_URL": source["SUPABASE_URL"],
        "CHATTY_MIGRATION_SOURCE_STORAGE_KEY": source.get("SUPABASE_SERVICE_ROLE_KEY") or source.get("SUPABASE_KEY", ""),
        "CHATTY_CENTRAL_STORAGE_URL": os.environ["SUPABASE_URL"],
        "CATALOG_STORAGE_BUCKET": source.get("CATALOG_STORAGE_BUCKET", "catalogos"),
        "CRM_MEDIA_BUCKET": source.get("CRM_MEDIA_BUCKET", "crm-private-media"),
    }
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def source_call(row, action, epoch, *, hold=False):
    business = row["business_id"]
    return call(row["bot_api_url"], runtime_token(business),
                "/internal/runtime/migration/" + action,
                None if action == "status" else {"epoch": epoch, "hold": hold})


def inspect_barrier(registry, epoch):
    evidence = {"bots": {}, "apis": {}, "workers": {}}
    for row in registry.all():
        status = source_call(row, "status", epoch)
        if (status.get("business_id") != row["business_id"] or status.get("mode") != "legacy"
                or status.get("channel_id") != row["channel_id"] or not status.get("frozen")
                or status.get("epoch") != epoch or status.get("workers_busy") != 0
                or status.get("requests_active") != 0
                or status.get("storage_origin", "").rstrip("/") != row["source_configuration"]["SUPABASE_URL"].rstrip("/")):
            raise CopyError("Los bots todavía no confirman el drenado y el origen esperado.")
        evidence["bots"][row["business_id"]] = status
    for role in API_ROLES:
        row = registry.service(role)
        status = call(row["api_url"], runtime_token(role, "service"), "/internal/runtime/service/status")
        if not status.get("frozen") or status.get("epoch") != epoch or status.get("inflight") != 0:
            raise CopyError("Una API todavía tiene operaciones en curso.")
        evidence["apis"][role] = status
    for role in WORKER_ROLES:
        row = registry.service(role)
        if not row["frozen"] or row["epoch"] != epoch or row["paused_epoch"] != epoch:
            raise CopyError("Un sincronizador todavía no ha confirmado su pausa.")
        evidence["workers"][role] = {key: row[key] for key in ("epoch", "paused_epoch", "frozen")}
    return evidence


def execute(body):
    registry = Registry()
    if body.action == "status":
        with connect("CHATTY_CENTRAL_DATABASE_URL", central=True) as target:
            state, progress = run_row(target, body.epoch)
            return {"state": state, "rows_installed": bool(progress.get("rows_installed")),
                    "barrier_verified": bool(progress.get("barrier_verified")),
                    "datasets": {name: {"final": item["final"],
                        "rows": sum(t["rows"] for t in item["tables"].values()),
                        "storage": item.get("storage")} for name, item in progress.get("datasets", {}).items()}}
    if body.action in {"prepare", "final-copy"}:
        if body.dataset not in DATASETS:
            raise CopyError("Selecciona un origen concreto.")
        final = body.action == "final-copy"
        if body.dataset in CRM_DATASETS:
            role = "commercial-api" if body.dataset == "commercial-crm" else "pilot-api"
            row = registry.service(role)
            return call(row["api_url"], runtime_token(role, "service"), "/internal/runtime/service/copy",
                        {"epoch": body.epoch, "final": final})
        row = registry.get(body.dataset)
        with source_environment(row):
            check_bot_source(body.dataset)
            with connect("CHATTY_CENTRAL_DATABASE_URL", central=True) as target:
                if not final:
                    prepare_staging(target)
                with connect("CHATTY_MIGRATION_SOURCE_DATABASE_URL") as source:
                    count = prepare(source, target, body.epoch, body.dataset, final=final)
        return {"dataset": body.dataset, "rows": count, "final": final}
    if body.action == "freeze":
        with connect("CHATTY_CENTRAL_DATABASE_URL", central=True) as target:
            state, progress = run_row(target, body.epoch)
        if state != "preparing" or set(progress.get("datasets", {})) != set(DATASETS):
            raise CopyError("Completa las cinco copias previas antes de pausar los orígenes.")
        for role in API_ROLES:
            row = registry.service(role)
            call(row["api_url"], runtime_token(role, "service"), "/internal/runtime/service/freeze", {"epoch": body.epoch})
        for role in WORKER_ROLES:
            registry.service_update(role, {"frozen": True, "epoch": body.epoch, "paused_epoch": ""})
        for row in registry.all():
            source_call(row, "freeze", body.epoch)
        return {"state": "draining", "epoch": body.epoch}
    if body.action == "verify-barrier":
        evidence = inspect_barrier(registry, body.epoch)
        for row in registry.all():
            source_call(row, "freeze", body.epoch, hold=True)
        with connect("CHATTY_CENTRAL_DATABASE_URL", central=True) as target:
            with target.transaction():
                state, progress = run_row(target, body.epoch, lock=True)
                if state != "preparing":
                    raise CopyError("El corte no está en preparación.")
                progress.update({"barrier_verified": True, "barrier_evidence": evidence})
                save_progress(target, body.epoch, progress)
                target.execute("UPDATE public.chatty_migration_runs SET state='frozen' WHERE epoch=%s", (body.epoch,))
        return {"state": "frozen", "barrier_verified": True}
    if body.action == "install":
        inspect_barrier(registry, body.epoch)
        with connect("CHATTY_CENTRAL_DATABASE_URL", central=True) as target:
            install(target, body.epoch)
        return {"rows_installed": True, "writers_enabled": False}
    if body.action == "abort":
        if any(row["mode"] != "legacy" for row in registry.all()):
            raise CopyError("Ya existe autoridad central; no se puede volver al origen antiguo.")
        for row in registry.all():
            status = source_call(row, "status", body.epoch)
            if status.get("frozen"):
                source_call(row, "resume", body.epoch)
        for role in API_ROLES:
            row = registry.service(role)
            if row["frozen"]:
                call(row["api_url"], runtime_token(role, "service"), "/internal/runtime/service/resume", {"epoch": body.epoch})
        for role in WORKER_ROLES:
            registry.service_update(role, {"frozen": False, "epoch": "", "paused_epoch": ""})
        with connect("CHATTY_CENTRAL_DATABASE_URL", central=True) as target:
            target.execute("UPDATE public.chatty_migration_runs SET state='aborted' WHERE epoch=%s", (body.epoch,))
        return {"state": "aborted", "origins_resumed": True}
    raise CopyError("Operación no admitida.")


def create_router():
    router = APIRouter()

    @router.post("/internal/runtime/migration/operation")
    def operation(body: Operation, request: Request):
        if (request.headers.get("origin") or request.headers.get("sec-fetch-site")
                or not hmac.compare_digest(request.headers.get("x-chatty-runtime-token", ""),
                                           runtime_token("coordinator", "service"))):
            raise HTTPException(401, "central_migration_authentication_required")
        if not COPY_LOCK.acquire(blocking=False):
            raise HTTPException(409, "central_migration_operation_running")
        try:
            return execute(body)
        except CopyError as error:
            raise HTTPException(409, str(error)) from None
        except Exception as error:
            raise HTTPException(503, "central_migration_failed_" + type(error).__name__) from None
        finally:
            COPY_LOCK.release()

    return router
