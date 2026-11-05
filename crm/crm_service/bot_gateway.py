"""Authenticated, tenant-scoped bot commands. The CRM never sends to Meta."""

import re
from collections.abc import Iterator
from typing import IO, Any
from urllib.parse import quote

import httpx

from crm_service.media import attachment_id, safe_attachment
from crm_service.settings import Business, bot_origin


class BotGatewayError(Exception):
    def __init__(self, code: str, status_code: int = 503, *, ambiguous: bool = False):
        self.code, self.status_code, self.ambiguous = code, status_code, ambiguous
        super().__init__(code)


class BotGateway:
    def __init__(
        self,
        business: Business,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20,
        test_mode: bool = False,
    ):
        if not business.operations_enabled or len(business.bot_control_secret) < 32:
            raise BotGatewayError("human_control_not_enabled", 409)
        try:
            origin = bot_origin(business.bot_api_url, test_mode=test_mode)
        except ValueError:
            raise BotGatewayError("invalid_bot_origin", 503) from None
        self.business = business
        self.client = httpx.Client(
            base_url=origin,
            transport=transport,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5)),
            follow_redirects=False,
            headers={"X-CRM-Bot-Secret": business.bot_control_secret},
        )

    def close(self) -> None:
        self.client.close()

    def _conversation_path(self, phone: str) -> str:
        if (
            not phone
            or phone in {".", ".."}
            or len(phone) > 200
            or any(ord(c) < 32 or c in "/\\" for c in phone)
        ):
            raise BotGatewayError("invalid_conversation_identity", 422)
        return f"/internal/crm/v1/businesses/{quote(self.business.id, safe='')}/conversations/{quote(phone, safe='')}"

    def _request(self, method: str, path: str, *, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self.client.request(method, path, json=body)
        except httpx.HTTPError:
            raise BotGatewayError(
                "bot_command_outcome_unknown" if method == "POST" else "bot_unavailable",
                202 if method == "POST" else 503,
                ambiguous=method == "POST",
            ) from None
        if not 200 <= response.status_code < 300:
            if method == "GET" and "/commands/" in path and response.status_code == 404:
                try:
                    missing_command = response.json() == {"detail": "command_not_found"}
                except ValueError:
                    missing_command = False
                if missing_command:
                    raise BotGatewayError("bot_command_not_found", 404)
            if method == "GET" and path.endswith("/control") and response.status_code == 404:
                try:
                    missing = response.json() == {"detail": "conversation_not_found"}
                except ValueError:
                    missing = False
                if missing:
                    raise BotGatewayError("conversation_state_not_found", 404)
            if response.status_code in {400, 401, 403, 404, 409, 413, 415, 416, 422, 423}:
                raise BotGatewayError(f"bot_rejected_{response.status_code}", response.status_code)
            raise BotGatewayError(
                "bot_command_outcome_unknown" if method == "POST" else "bot_unavailable",
                202 if method == "POST" else 503,
                ambiguous=method == "POST",
            )
        try:
            data = response.json()
        except ValueError:
            raise BotGatewayError(
                "invalid_bot_response", 202 if method == "POST" else 502, ambiguous=method == "POST"
            ) from None
        if not isinstance(data, dict):
            raise BotGatewayError("invalid_bot_response", 502, ambiguous=method == "POST")
        return data

    def _validate_control(self, data: dict[str, Any], phone: str) -> dict[str, Any]:
        if (
            data.get("business_id") != self.business.id
            or data.get("phone_number") != phone
            or data.get("provider") != "crm"
            or type(data.get("version")) is not int
            or data.get("phase") not in {"ai", "waiting", "pausing", "human"}
            or type(data.get("is_paused")) is not bool
            or type(data.get("confirmed")) is not bool
            or type(data.get("can_send")) is not bool
        ):
            raise BotGatewayError("bot_scope_or_control_mismatch", 502)
        return {
            name: data.get(name)
            for name in (
                "business_id",
                "phone_number",
                "provider",
                "phase",
                "version",
                "owner",
                "is_paused",
                "confirmed",
                "can_send",
                "window_expires_at",
            )
        }

    def control(self, phone: str) -> dict[str, Any]:
        return self._validate_control(
            self._request("GET", self._conversation_path(phone) + "/control"), phone
        )

    def media_capabilities(self) -> dict[str, Any]:
        data = self._request(
            "GET", f"/internal/crm/v1/businesses/{quote(self.business.id, safe='')}/media-capabilities"
        )
        if data.get("business_id") != self.business.id:
            raise BotGatewayError("bot_media_scope_mismatch", 502)
        if type(data.get("media_enabled")) is not bool or type(data.get("storage_ready")) is not bool:
            raise BotGatewayError("invalid_bot_media_capabilities", 502)
        return data

    def attachment(self, phone: str, asset_id: str, actor: str = "") -> dict[str, Any]:
        asset_id = attachment_id(asset_id)
        data = self._request("GET", self._conversation_path(phone) + "/attachments/" + asset_id)
        try:
            return safe_attachment(data, self.business.id, phone, asset_id)
        except (ValueError, TypeError):
            raise BotGatewayError("invalid_bot_media_response", 502) from None

    def upload_attachment(
        self,
        phone: str,
        upload_id: str,
        filename: str,
        expected_version: int,
        actor: str,
        content_type: str,
        size: int,
        voice_note: bool,
        source: IO[bytes],
    ) -> dict[str, Any]:
        upload_id = attachment_id(upload_id)

        def chunks() -> Iterator[bytes]:
            while block := source.read(64 * 1024):
                yield block

        try:
            response = self.client.post(
                self._conversation_path(phone) + "/attachments",
                params={
                    "upload_id": upload_id,
                    "filename": filename,
                    "expected_version": expected_version,
                    "voice_note": str(voice_note).lower(),
                },
                headers={
                    "Content-Type": content_type,
                    "Content-Length": str(size),
                    "X-Upload-Size": str(size),
                    "X-CRM-Actor": actor,
                },
                content=chunks(),
                timeout=httpx.Timeout(180, connect=5),
            )
        except httpx.HTTPError:
            # This is a private file upload, never a request to send a WhatsApp message.
            # No retry: the caller may query the immutable upload ID to discover its state.
            raise BotGatewayError("attachment_upload_outcome_unknown", 503) from None
        if not 200 <= response.status_code < 300:
            status = (
                response.status_code
                if response.status_code in {400, 401, 403, 404, 409, 413, 415, 422}
                else 503
            )
            raise BotGatewayError("attachment_upload_rejected", status)
        try:
            return safe_attachment(response.json(), self.business.id, phone, upload_id)
        except (ValueError, TypeError):
            raise BotGatewayError("invalid_bot_media_response", 502) from None

    def attachment_content(
        self,
        phone: str,
        asset_id: str,
        headers: dict[str, str],
        variant: str = "original",
    ) -> httpx.Response:
        asset_id = attachment_id(asset_id)
        if variant not in {"original", "playback"}:
            raise BotGatewayError("invalid_attachment_variant", 422)
        request = self.client.build_request(
            "GET",
            self._conversation_path(phone) + "/attachments/" + asset_id + "/content",
            params={"variant": variant},
            headers=headers,
        )
        try:
            response = self.client.send(request, stream=True)
        except httpx.HTTPError:
            raise BotGatewayError("attachment_unavailable", 503) from None
        if response.status_code not in {200, 206, 416}:
            status = (
                response.status_code if response.status_code in {400, 401, 403, 404, 409, 410, 422} else 503
            )
            response.close()
            raise BotGatewayError("attachment_unavailable", status)
        return response

    def command(self, phone: str, command_id: str) -> dict[str, Any]:
        result = self._request(
            "GET", self._conversation_path(phone) + "/commands/" + quote(command_id, safe="")
        )
        return self._validate_result(result, phone, command_id)

    def _validate_result(self, result: dict[str, Any], phone: str, command_id: str) -> dict[str, Any]:
        if result.get("command_id") != command_id or not isinstance(result.get("control"), dict):
            raise BotGatewayError("bot_command_identity_mismatch", 502, ambiguous=True)
        control = self._validate_control(result["control"], phone)
        if result.get("status") not in {
            "confirmed",
            "pending",
            "accepted",
            "delivered",
            "read",
            "failed",
            "unknown",
        }:
            raise BotGatewayError("invalid_bot_command_status", 502, ambiguous=True)
        provider_id = result.get("provider_message_id")
        if provider_id is not None and (not isinstance(provider_id, str) or len(provider_id) > 300):
            raise BotGatewayError("invalid_provider_message_identity", 502, ambiguous=True)
        safe = {
            "command_id": command_id,
            "status": result["status"],
            "control": control,
            "provider_message_id": provider_id,
        }
        if result.get("error"):
            error = str(result["error"])
            safe["error"] = (
                error if re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", error) else "bot_operation_failed"
            )
        return safe

    def execute(self, operation: str, phone: str, body: dict[str, Any]) -> dict[str, Any]:
        if operation not in {"take", "messages", "close"}:
            raise BotGatewayError("unsupported_bot_operation", 422)
        result = self._request("POST", self._conversation_path(phone) + "/" + operation, body=body)
        try:
            return self._validate_result(result, phone, body["command_id"])
        except BotGatewayError as error:
            raise BotGatewayError(error.code, 202, ambiguous=True) from None
