"""Authenticated bot-side human operations. This router never implements Meta transport."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from human_control_store import ControlError
from human_support import uses_crm
from observability import current_trace, message_trace
from provider_errors import ProviderError
from webhook_utils import valid_whatsapp_sender


class ControlCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    actor: str = Field(min_length=1, max_length=200)
    command_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    expected_version: int = Field(ge=0)


class TakeCommand(ControlCommand):
    reason: str = Field(default="CRM human takeover", min_length=1, max_length=255)


class ReplyCommand(ControlCommand):
    text: str = Field(min_length=1, max_length=4096)


class CloseCommand(ControlCommand):
    reason: str = Field(default="CRM case closed", min_length=1, max_length=255)


def create_router(
    config: Any,
    store_factory: Any,
    send_text: Any,
    get_logs: Any,
    invalidate_followup: Any,
    phone_lock: Any,
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

    @router.post(base + "/messages")
    def message(
        business_id: str, phone: str, body: ReplyCommand, request: Request
    ) -> JSONResponse:
        authorize(request, business_id)
        if not valid_whatsapp_sender(phone):
            raise HTTPException(422, "Invalid WhatsApp recipient")
        store = store_factory()
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
            value = call(
                store,
                "begin_send",
                phone,
                body.command_id,
                "human",
                body.actor,
                body.expected_version,
                _digest(body),
                body.text,
                prior.get("control", {}).get("inbound_message_ids") or [],
            )
            return mutation_response(value)
        with phone_lock(phone):
            snapshot = call(store, "get_control", phone)
            value = call(
                store,
                "begin_send",
                phone,
                body.command_id,
                "human",
                body.actor,
                body.expected_version,
                _digest(body),
                body.text,
                snapshot.get("inbound_message_ids") or [],
            )
            if value.get("status") != "reserved":
                return mutation_response(value)
            snapshot = value["control"]
            with message_trace(
                source="human",
                source_event_id=body.command_id,
                inbound_message_ids=snapshot.get("inbound_message_ids") or [],
                crm_reserved_attempt=body.command_id,
                meta_window_expires_at=snapshot.get("window_expires_at"),
            ):
                try:
                    accepted = send_text(phone, body.text)
                    provider_id = str(current_trace().get("provider_message_id") or "")
                    status = "accepted" if accepted and provider_id else "unknown"
                    detail = (
                        ""
                        if status == "accepted"
                        else "provider_acknowledgement_missing"
                    )
                except ProviderError as error:
                    status = (
                        "failed"
                        if error.confirmed_not_sent
                        or (error.status is not None and 400 <= error.status < 500)
                        else "unknown"
                    )
                    provider_id, detail = (
                        "",
                        "provider_rejected"
                        if status == "failed"
                        else "provider_outcome_unknown",
                    )
                except Exception:
                    status, provider_id, detail = (
                        "unknown",
                        "",
                        "provider_outcome_unknown",
                    )
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


def _digest(body: ReplyCommand) -> str:
    return hashlib.sha256(
        json.dumps(body.model_dump(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
