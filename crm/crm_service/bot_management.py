"""Central, credential-private access to the existing bot management API."""

import re
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import and_, or_, select
from starlette.background import BackgroundTask

from crm_service.models import CRMConversation, SourceMessage
from crm_service.routing import queue_clause
from crm_service.runtime_registry import Registry, RegistryError
from crm_service.security import Actor
from crm_service.settings import bot_origin

PATHS = {
    "api/current-si": {"GET"}, "api/generate-si-changes": {"POST"},
    "api/format-and-save-si": {"POST"}, "api/si-history": {"GET"},
    "api/current-catalog": {"GET"}, "api/upload-catalog": {"POST"},
    "api/catalogs": {"GET", "POST"}, "api/catalog-prompt-preview": {"GET"},
}
CATALOG = re.compile(r"^api/catalogs/[A-Za-z0-9_-]{1,80}(/file)?$")
SI_JOB = re.compile(r"^api/si-job/[a-fA-F0-9-]{36}$")
RESERVED = {"client_name", "backend", "backend_url", "api_key"}


def authorize_management(principal: Actor, business: str) -> None:
    principal.authorize(business)
    original = "memos" if principal.login_profile == "memos_mayoristas" else principal.login_profile
    if not principal.is_admin and original != business:
        # Shared Tanaka/Velvet attention does not grant shared bot editing.
        raise HTTPException(403, "Tu cuenta no puede gestionar este bot.")


def management_path_allowed(path: str, method: str) -> bool:
    if path in PATHS:
        return method in PATHS[path]
    if SI_JOB.fullmatch(path):
        return method == "GET"
    match = CATALOG.fullmatch(path)
    return bool(match and method in ({"GET", "POST"} if match.group(1) else {"PATCH", "DELETE"}))


def create_router(settings, actor, sessions) -> APIRouter:
    router = APIRouter()
    Auth = Annotated[Actor, Depends(actor)]

    def target(business: str) -> tuple[str, str]:
        if settings.database_schema != "chatty_crm":
            raise HTTPException(503, "La gestión central todavía no está habilitada.")
        try:
            row = Registry().get(business)
            key = row["source_configuration"].get("DASHBOARD_API_KEY", "").strip()
            if row["mode"] != "central" or not key or key == "PLACEHOLDER_VALUE_TO_BE_REPLACED":
                raise RegistryError("management_connection_missing")
            return bot_origin(row["bot_api_url"], test_mode=settings.bot_test_mode), key
        except (RegistryError, KeyError, ValueError, TypeError):
            raise HTTPException(503, "La conexión de gestión de este bot no está disponible en el registro central.") from None

    @router.get("/v1/bot-management/{business_id}/config")
    def configuration(business_id: str, principal: Auth):
        authorize_management(principal, business_id)
        target(business_id)
        return {"configured": True, "mode": "central"}

    @router.get("/v1/bot-management/{business_id}/logs")
    def logs(
        business_id: str, principal: Auth,
        q: str = Query(default="", max_length=100),
        phone: str = Query(default="", pattern=r"^[0-9]{0,30}$"),
    ):
        principal.authorize(business_id)
        if settings.database_schema != "chatty_crm":
            raise HTTPException(503, "El historial central todavía no está habilitado.")
        with sessions() as session:
            query = select(SourceMessage).join(CRMConversation, and_(
                CRMConversation.business_id == SourceMessage.business_id,
                CRMConversation.phone_number == SourceMessage.phone_number,
            )).where(SourceMessage.business_id == business_id, queue_clause(principal))
            if phone:
                query = query.where(SourceMessage.phone_number == phone)
            elif len(q.strip()) >= 2:
                escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                matches = [SourceMessage.content.ilike(f"%{escaped}%", escape="\\")]
                digits = re.sub(r"[^0-9]", "", q)
                if len(digits) >= 3:
                    matches.append(SourceMessage.phone_number.ilike(f"%{digits}%"))
                found = session.scalars(query.where(or_(*matches)).order_by(
                    SourceMessage.source_id.desc()).limit(500)).all()
                phones = list(dict.fromkeys(row.phone_number for row in found))[:30]
                if not phones:
                    return {"rows": []}
                query = query.where(SourceMessage.phone_number.in_(phones))
            rows = session.scalars(query.order_by(
                SourceMessage.source_id.asc() if phone else SourceMessage.source_id.desc(),
            ).limit(5000 if phone or len(q.strip()) >= 2 else 2000))
            return {"rows": [{"id": row.source_id, "phone_number": row.phone_number,
                              "role": row.role, "content": row.content,
                              "created_at": row.created_at.isoformat()} for row in rows]}

    @router.api_route("/v1/bot-management/{business_id}/{path:path}",
                      methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def relay(business_id: str, path: str, request: Request, principal: Auth):
        authorize_management(principal, business_id)
        if not management_path_allowed(path, request.method):
            raise HTTPException(404, "Operación de gestión no permitida.")
        # No database session is held across the upstream request or download.
        from starlette.concurrency import run_in_threadpool
        origin, key = await run_in_threadpool(target, business_id)
        params = [(name, value) for name, value in request.query_params.multi_items()
                  if name.lower() not in RESERVED]
        params.append(("client_name", business_id))
        headers = {"X-Dashboard-API-Key": key, "X-Client-Name": business_id}
        for name in ("accept", "content-type", "range"):
            if request.headers.get(name):
                headers[name] = request.headers[name]
        client = httpx.AsyncClient(timeout=httpx.Timeout(180, connect=5), follow_redirects=False)
        try:
            upstream = await client.send(client.build_request(
                request.method, origin + "/" + path, params=params, headers=headers,
                content=request.stream() if request.method not in {"GET", "HEAD"} else None,
            ), stream=True)
        except httpx.HTTPError:
            await client.aclose()
            message = ("No se pudo leer el servidor del bot. Vuelve a consultar."
                       if request.method == "GET" else
                       "No se pudo confirmar el cambio. Consulta el contenido actual antes de volver a guardarlo.")
            raise HTTPException(502, message) from None
        except Exception:
            await client.aclose()
            raise

        async def close():
            await upstream.aclose()
            await client.aclose()

        if upstream.status_code >= 300:
            detail = f"El servidor del bot respondió HTTP {upstream.status_code}."
            try:
                await upstream.aread()
                payload = upstream.json()
                value = payload.get("detail", payload.get("error")) if isinstance(payload, dict) else None
                if isinstance(value, str) and "<html" not in value.lower() and "<!doctype" not in value.lower():
                    detail = value.replace(key, "[credencial oculta]")[:800]
            except (ValueError, httpx.HTTPError):
                pass
            finally:
                await close()
            return JSONResponse({"detail": detail}, status_code=upstream.status_code if upstream.status_code >= 400 else 502)
        safe_headers = {"Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff"}
        for name in ("content-type", "content-disposition", "content-range", "accept-ranges"):
            if upstream.headers.get(name):
                safe_headers[name] = upstream.headers[name]
        # HTTPX decodes content: do not forward its old encoding/length headers.
        async def content():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await close()

        return StreamingResponse(content(), status_code=upstream.status_code,
                                 headers=safe_headers, background=BackgroundTask(close))

    return router
