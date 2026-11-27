"""Bot-owned durable conversation control. CRM callers never access these RPCs directly."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


META_THREAD_CONTROL_ERROR = "meta_thread_control_131070"


class ControlError(RuntimeError):
    def __init__(self, code: str, status_code: int = 409):
        self.code, self.status_code = code, status_code
        super().__init__(code)


class ControlStore:
    def __init__(
        self, rpc_executor: Callable[[str, dict[str, Any]], Any], business_id: str,
        *, read_send_detail: Callable[[str, str], str | None] | None = None,
    ):
        self.rpc_executor = rpc_executor
        self.business_id = business_id
        self.read_send_detail = read_send_detail

    def _call(
        self,
        phone: str | None,
        action: str,
        request_id: str = "",
        actor: str = "",
        version: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self.rpc_executor(
            "crm_control_dispatch",
            {
                "p_business_id": self.business_id,
                "p_phone": phone,
                "p_action": action,
                "p_request_id": request_id,
                "p_actor": actor,
                "p_expected_version": version,
                "p_payload": payload or {},
            },
        )
        if isinstance(result, list):
            result = result[0] if result else None
        if not isinstance(result, dict):
            raise ControlError("control_store_invalid_response", 503)
        if result.get("error"):
            raise ControlError(
                str(result["error"]), int(result.get("http_status", 409))
            )
        return result

    def get_control(self, phone: str) -> dict[str, Any]:
        return self._call(phone, "read")

    def get_command(self, phone: str, command_id: str) -> dict[str, Any]:
        result = self._call(phone, "command", command_id)
        if result.get("status") != "failed" or self.read_send_detail is None:
            return result
        try:
            detail = self.read_send_detail(phone, command_id)
        except Exception:
            # An unavailable diagnostic must not make a settled send uncertain.
            return result
        if detail == META_THREAD_CONTROL_ERROR:
            return {**result, "error": detail}
        return result

    def apply_control(
        self,
        phone: str,
        operation: str,
        command_id: str,
        actor: str = "",
        expected_version: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if operation not in {"take", "close", "handoff", "reset", "transfer"}:
            raise ControlError("unsupported_control_operation", 400)
        return self._call(
            phone, operation, command_id, actor, expected_version, payload
        )

    def begin_send(
        self,
        phone: str,
        attempt_id: str,
        kind: str,
        actor: str = "",
        expected_version: int | None = None,
        request_digest: str = "",
        text: str = "",
        inbound_message_ids: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        handoff_request_id: str = "",
    ) -> dict[str, Any]:
        return self._call(
            phone,
            "begin_send",
            attempt_id,
            actor,
            expected_version,
            {
                "kind": kind,
                "request_digest": request_digest,
                "text": text,
                "inbound_message_ids": inbound_message_ids or [],
                **({"attachments": attachments} if attachments else {}),
                **({"purpose": "handoff_ack", "handoff_request_id": handoff_request_id}
                   if handoff_request_id else {}),
            },
        )

    def finish_send(
        self,
        attempt_id: str,
        status: str,
        provider_message_id: str = "",
        detail: str = "",
        phone: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            phone,
            "finish_send",
            attempt_id,
            payload={
                "status": status,
                "provider_message_id": provider_message_id,
                "detail": detail[:100],
            },
        )

    def ack_provider_receipt(
        self, provider_message_id: str, status: str, attempt_id: str = ""
    ) -> dict[str, Any]:
        return self._call(
            None,
            "receipt",
            attempt_id,
            payload={"status": status, "provider_message_id": provider_message_id},
        )


def build_runtime_store(supabase: Any, business_id: str) -> ControlStore:
    def read_send_detail(phone: str, command_id: str) -> str | None:
        rows = (
            supabase.table("crm_send_attempts").select("detail")
            .eq("business_id", business_id).eq("phone_number", phone)
            .eq("attempt_id", command_id).eq("kind", "human").eq("status", "failed")
            .limit(1).execute().data
        )
        return rows[0].get("detail") if rows else None

    return ControlStore(
        lambda name, params: supabase.rpc(name, params).execute().data,
        business_id,
        read_send_detail=read_send_detail,
    )


def runtime_store() -> ControlStore:
    from config import config
    from database import supabase

    return build_runtime_store(supabase, config.BUSINESS_ID)


def get_control(phone: str) -> dict[str, Any]:
    return runtime_store().get_control(phone)


def apply_control(
    phone: str,
    operation: str,
    command_id: str,
    actor: str = "",
    expected_version: int | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return runtime_store().apply_control(
        phone, operation, command_id, actor, expected_version, payload
    )
