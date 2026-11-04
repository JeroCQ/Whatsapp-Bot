"""Bot-owned durable conversation control. CRM callers never access these RPCs directly."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class ControlError(RuntimeError):
    def __init__(self, code: str, status_code: int = 409):
        self.code, self.status_code = code, status_code
        super().__init__(code)


class ControlStore:
    def __init__(
        self, rpc_executor: Callable[[str, dict[str, Any]], Any], business_id: str
    ):
        self.rpc_executor = rpc_executor
        self.business_id = business_id

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
        return self._call(phone, "command", command_id)

    def apply_control(
        self,
        phone: str,
        operation: str,
        command_id: str,
        actor: str = "",
        expected_version: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if operation not in {"take", "close", "handoff"}:
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
    return ControlStore(
        lambda name, params: supabase.rpc(name, params).execute().data, business_id
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
