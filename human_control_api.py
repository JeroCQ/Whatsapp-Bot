"""Authenticated bot-side human operations. This router never implements Meta transport."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import tempfile
from functools import partial
from typing import Any

import anyio
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from human_control_store import ControlError
from human_support import uses_crm
from observability import current_trace, message_trace
from provider_errors import ProviderError
from webhook_utils import valid_whatsapp_sender

logger = logging.getLogger(__name__)


class ControlCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    actor: str = Field(min_length=1, max_length=200)
    command_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    expected_version: int = Field(ge=0)


class TakeCommand(ControlCommand):
    reason: str = Field(default="CRM human takeover", min_length=1, max_length=255)


class ReplyCommand(ControlCommand):
    text: str = Field(default="", max_length=4096)
    attachment_id: str | None = Field(default=None, min_length=36, max_length=36)

    @model_validator(mode="after")
    def message_content(self) -> "ReplyCommand":
        if not self.text and not self.attachment_id:
            raise ValueError("A message needs text or an attachment")
        return self


class CloseCommand(ControlCommand):
    reason: str = Field(default="CRM case closed", min_length=1, max_length=255)


class TransferCommand(ControlCommand):
    target_actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(default="Operator transfer", min_length=1, max_length=255)


def create_router(
    config: Any,
    store_factory: Any,
    send_text: Any,
    get_logs: Any,
    invalidate_followup: Any,
    phone_lock: Any,
    *,
    media_factory: Any = None,
    send_media: Any = None,
) -> APIRouter:
    """Callbacks are the bot's existing send/history/coordination functions."""
    router = APIRouter()
    base = "/internal/crm/v1/businesses/{business_id}/conversations/{phone}"

    def authorize(request: Request, business_id: str) -> None:
        if not getattr(config, "CRM_CONTROL_ENABLED", False) or not uses_crm(config):
            raise HTTPException(404, "CRM control is not enabled")
        secret = str(getattr(config, "CRM_BOT_CONTROL_SECRET", "") or "")
        if (
            request.headers.get("origin")
            or request.headers.get("sec-fetch-site")
            or len(secret) < 32
            or not hmac.compare_digest(
                request.headers.get("x-crm-bot-secret", ""), secret
            )
        ):
            raise HTTPException(401, "Bot control authentication required")
        if business_id != config.BUSINESS_ID:
            raise HTTPException(403, "Business does not match this bot deployment")

    def call(store: Any, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return getattr(store, method)(*args, **kwargs)
        except ControlError as error:
            raise HTTPException(error.status_code, error.code) from None
        except Exception:
            raise HTTPException(
                503, "Authoritative conversation control unavailable"
            ) from None

    def mutation_response(value: dict[str, Any]) -> JSONResponse:
        from fastapi.encoders import jsonable_encoder

        value = dict(value)
        if value.get("status") == "sending":
            value["status"] = "pending"
        status_code = 202 if value.get("status") in {"pending", "unknown"} else 200
        return JSONResponse(jsonable_encoder(value), status_code=status_code)

    def media_service() -> Any:
        from crm_media import media_enabled
        if not media_factory or not media_enabled(config):
            raise HTTPException(404, "CRM media is not enabled")
        return media_factory()

    def media_failure(error: Exception) -> HTTPException:
        from crm_media_policy import MediaError
        if isinstance(error, MediaError):
            if error.diagnostic:
                # Only bounded codec/stage tokens; never ffmpeg stderr, paths,
                # request headers, filenames, message content or credentials.
                logger.warning(
                    "CRM media rejected: code=%s stage=%s mime=%s codec=%s",
                    error.code, error.diagnostic.get("stage", "unknown"),
                    error.diagnostic.get("mime_type", "unknown"),
                    error.diagnostic.get("audio_codec", "unknown"),
                )
            return HTTPException(error.status_code, error.code, headers=getattr(error, "headers", None))
        if isinstance(error, ControlError):
            return HTTPException(error.status_code, error.code)
        return HTTPException(503, "private_media_unavailable")

    @router.get("/internal/crm/v1/businesses/{business_id}/media-capabilities")
    def media_capabilities(business_id: str, request: Request) -> dict[str, Any]:
        authorize(request, business_id)
        from crm_media import media_enabled
        from crm_media_policy import LIMITS
        if not media_factory or not media_enabled(config):
            return {"business_id": business_id, "media_enabled": False, "storage_ready": False, "reason": "crm_media_not_enabled", "limits": LIMITS}
        return media_factory().readiness()

    @router.post(base + "/attachments")
    async def attachment_upload(
        business_id: str, phone: str, request: Request,
        upload_id: str = Query(min_length=36, max_length=36),
        filename: str = Query(min_length=1),
        expected_version: int = Query(ge=0), voice_note: bool = False,
    ) -> dict[str, Any]:
        authorize(request, business_id)
        from crm_media import canonical_id, require_human_control
        from crm_media_policy import safe_filename, upload_limit
        service = media_service()
        actor = request.headers.get("x-crm-actor", "")
        if not actor or len(actor) > 200:
            raise HTTPException(401, "CRM actor authentication required")
        mime = request.headers.get("content-type", "")
        try:
            canonical_id(upload_id)
            safe_filename(filename)
            maximum = upload_limit(mime)
            declared = int(request.headers.get("x-upload-size", ""))
            if declared < 1 or declared > maximum:
                raise HTTPException(413, "attachment_too_large")
            store = store_factory()
            await anyio.to_thread.run_sync(require_human_control, store, phone, actor, expected_version)
            with tempfile.NamedTemporaryFile(mode="w+b") as stream:
                actual = 0
                async for chunk in request.stream():
                    actual += len(chunk)
                    if actual > maximum or actual > declared:
                        raise HTTPException(413, "attachment_too_large")
                    stream.write(chunk)
                if actual != declared:
                    raise HTTPException(422, "attachment_length_mismatch")
                stream.flush()
                result = await anyio.to_thread.run_sync(partial(
                    service.upload, phone, upload_id, actor, stream.name, mime, filename, voice_note=voice_note,
                ))
                await anyio.to_thread.run_sync(require_human_control, store, phone, actor, expected_version)
                return result
        except HTTPException:
            raise
        except ValueError as error:
            raise media_failure(error) if hasattr(error, "code") else HTTPException(422, "invalid_upload_size") from None
        except Exception as error:
            raise media_failure(error) from None

    @router.get(base + "/attachments/{attachment_id}")
    def attachment_metadata(business_id: str, phone: str, attachment_id: str, request: Request) -> dict[str, Any]:
        authorize(request, business_id)
        try:
            return media_service().metadata(phone, attachment_id)
        except HTTPException:
            raise
        except Exception as error:
            raise media_failure(error) from None

    @router.get(base + "/attachments/{attachment_id}/content")
    def attachment_content(
        business_id: str, phone: str, attachment_id: str, request: Request, variant: str = "original",
    ) -> StreamingResponse:
        authorize(request, business_id)
        try:
            iterator, status, headers = media_service().content(phone, attachment_id,
                range_header=request.headers.get("range"), if_range=request.headers.get("if-range"), variant=variant)
            return StreamingResponse(iterator, status_code=status, headers=headers)
        except HTTPException:
            raise
        except Exception as error:
            raise media_failure(error) from None

    @router.get(base + "/control")
    def control(business_id: str, phone: str, request: Request) -> dict[str, Any]:
        authorize(request, business_id)
        return call(store_factory(), "get_control", phone)

    @router.get(base + "/commands/{command_id}")
    def command(
        business_id: str, phone: str, command_id: str, request: Request
    ) -> JSONResponse:
        authorize(request, business_id)
        return mutation_response(
            call(store_factory(), "get_command", phone, command_id)
        )

    @router.post(base + "/take")
    def take(
        business_id: str, phone: str, body: TakeCommand, request: Request
    ) -> JSONResponse:
        authorize(request, business_id)
        value = call(
            store_factory(),
            "apply_control",
            phone,
            "take",
            body.command_id,
            body.actor,
            body.expected_version,
            {"reason": body.reason},
        )
        # The RPC already cancels tokens atomically. Retain the bot's existing cancellation hook.
        invalidate_followup(phone)
        return mutation_response(value)

    @router.post(base + "/close")
    def close(
        business_id: str, phone: str, body: CloseCommand, request: Request
    ) -> JSONResponse:
        authorize(request, business_id)
        value = call(
            store_factory(),
            "apply_control",
            phone,
            "close",
            body.command_id,
            body.actor,
            body.expected_version,
            {"reason": body.reason},
        )
        invalidate_followup(phone)
        return mutation_response(value)

    @router.post(base + "/transfer")
    def transfer(business_id: str, phone: str, body: TransferCommand, request: Request) -> JSONResponse:
        authorize(request, business_id)
        return mutation_response(call(store_factory(), "apply_control", phone, "transfer", body.command_id,
                                      body.actor, body.expected_version, {"target_actor": body.target_actor}))

    @router.post(base + "/messages")
    def message(
        business_id: str, phone: str, body: ReplyCommand, request: Request
    ) -> JSONResponse:
        authorize(request, business_id)
        if not valid_whatsapp_sender(phone):
            raise HTTPException(422, "Invalid WhatsApp recipient")
        store = store_factory()
        attachment = None
        service = None
        if body.attachment_id:
            from crm_media_policy import validate_caption
            try:
                service = media_service()
                attachment = service.outbound(phone, body.attachment_id, body.actor)
                validate_caption(attachment["kind"], body.text)
                if not send_media:
                    raise HTTPException(503, "media_sender_unavailable")
            except HTTPException:
                raise
            except Exception as error:
                raise media_failure(error) from None
        digest = _digest(body, attachment)

        def begin(inbound: list[str]) -> dict[str, Any]:
            args = [phone, body.command_id, "human", body.actor, body.expected_version, digest, body.text, inbound]
            if attachment:
                from crm_media import public_attachment
                args.append([public_attachment(attachment)])
            return call(store, "begin_send", *args)
        # Replayed provider results are never sent again, even if a later close changed the epoch.
        try:
            prior = store.get_command(phone, body.command_id)
        except ControlError as error:
            if error.status_code != 404:
                raise HTTPException(error.status_code, error.code) from None
        except Exception:
            raise HTTPException(
                503, "Authoritative conversation control unavailable"
            ) from None
        else:
            # begin_send compares the immutable original request, including actor/text/version.
            value = begin((prior.get("control") or {}).get("inbound_message_ids") or [])
            return mutation_response(value)
        with phone_lock(phone):
            snapshot = call(store, "get_control", phone)
            value = begin(snapshot.get("inbound_message_ids") or [])
            if value.get("status") != "reserved":
                return mutation_response(value)
            snapshot = value["control"]
            with message_trace(
                source="human",
                source_event_id=body.command_id,
                inbound_message_ids=snapshot.get("inbound_message_ids") or [],
                crm_reserved_attempt=body.command_id,
                provider_message_id="",
                meta_window_expires_at=snapshot.get("window_expires_at"),
            ):
                send_started = False

                def media_sender(*args: Any) -> Any:
                    nonlocal send_started
                    send_started = True
                    return send_media(*args)

                try:
                    if attachment:
                        service.send_attachment(phone, attachment, body.text, media_sender)
                    else:
                        send_started = True
                        send_text(phone, body.text)
                    provider_id = str(current_trace().get("provider_message_id") or "")
                    # Meta's message identity, not a wrapper's truthy return,
                    # proves acceptance. It never proves delivered/read.
                    status = "accepted" if provider_id else "unknown"
                    detail = (
                        ""
                        if status == "accepted"
                        else "provider_acknowledgement_missing"
                    )
                except ProviderError as error:
                    provider_id = str(current_trace().get("provider_message_id") or "")
                    status = "accepted" if provider_id else (
                        "failed"
                        if error.confirmed_not_sent
                        or (error.status is not None and 400 <= error.status < 500)
                        else "unknown"
                    )
                    detail = "" if provider_id else (
                        "provider_rejected" if status == "failed" else "provider_outcome_unknown"
                    )
                except Exception as error:
                    from crm_media_policy import MediaError
                    provider_id = str(current_trace().get("provider_message_id") or "")
                    if provider_id:
                        status, detail = "accepted", ""
                    elif isinstance(error, MediaError) and not send_started:
                        status, provider_id, detail = "failed", "", error.code
                    else:
                        status, provider_id, detail = "unknown", "", "provider_outcome_unknown"
                result = call(
                    store,
                    "finish_send",
                    body.command_id,
                    status,
                    provider_id,
                    detail,
                    phone=phone,
                )
            return mutation_response(result)

    return router


def _digest(body: ReplyCommand, attachment: dict[str, Any] | None = None) -> str:
    payload = body.model_dump(exclude_none=True)
    if attachment:
        payload["attachment_sha256"] = attachment["sha256"]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
