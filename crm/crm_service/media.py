"""Bounded, private bot-owned media transport policy; no storage credentials."""

import re
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

LIMITS = {
    "image": 5_000_000,
    "audio": 16_000_000,
    "video": 16_000_000,
    "document": 100_000_000,
    "sticker": 500_000,
}
MAX_UPLOAD_BYTES = LIMITS["document"]
MIMES = {
    "image/jpeg": "image",
    "image/png": "image",
    "image/webp": "sticker",
    "audio/aac": "audio",
    "audio/amr": "audio",
    "audio/mpeg": "audio",
    "audio/mp4": "audio",
    "audio/ogg": "audio",
    "audio/webm": "audio",
    "video/mp4": "video",
    "video/3gpp": "video",
    "application/pdf": "document",
    "text/plain": "document",
    "application/msword": "document",
    "application/vnd.ms-excel": "document",
    "application/vnd.ms-powerpoint": "document",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "document",
    # Browsers frequently omit Office MIME types. This is an untrusted upload
    # hint only; the bot identifies the bytes and applies the actual type limit.
    "application/octet-stream": "document",
}
_UPLOAD_PATH = re.compile(r"^/v1/conversations/[a-z][a-z0-9_]{0,62}/[^/]+/attachments$")


def attachment_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, "Invalid attachment identity") from None


def filename(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or any(ord(c) < 32 or ord(c) == 127 or c in "/\\" for c in value)
    ):
        raise HTTPException(422, "Invalid attachment filename")
    return value


def upload_parameters(content_type: str, size: str, voice_note: bool) -> tuple[str, int]:
    mime = content_type.split(";", 1)[0].strip().lower() or "application/octet-stream"
    if mime not in MIMES:
        # Preserve transfer of files whose browser MIME hint is unknown. Only
        # the bot can inspect their bytes and ask Meta to accept a document.
        mime = "application/octet-stream"
    if mime == "audio/webm" and not voice_note:
        raise HTTPException(415, "WebM audio must be converted before WhatsApp sending")
    if not re.fullmatch(r"[1-9][0-9]{0,11}", size):
        raise HTTPException(422, "A valid X-Upload-Size is required")
    count = int(size)
    if count > LIMITS[MIMES[mime]]:
        raise HTTPException(413, "Attachment exceeds WhatsApp media limit")
    return mime, count


def safe_attachment(data: Any, business: str, phone: str, expected_id: str) -> dict[str, Any]:
    """Fail closed on another scope or unsafe/unrecognised bot response fields."""
    if not isinstance(data, dict) or data.get("business_id") != business or data.get("phone_number") != phone:
        raise ValueError("attachment scope mismatch")
    if str(data.get("id")) != expected_id:
        raise ValueError("attachment identity mismatch")
    kind, mime, size = data.get("kind"), data.get("mime_type"), data.get("size_bytes")
    if (
        kind not in LIMITS
        or not isinstance(mime, str)
        or MIMES.get(mime) != kind
        or mime == "audio/webm"
        or type(size) is not int
        or not 0 <= size <= LIMITS[kind]
        or data.get("availability") not in {"pending", "ready", "unavailable"}
    ):
        raise ValueError("invalid attachment metadata")
    try:
        name = filename(str(data.get("filename") or ""))
    except HTTPException:
        raise ValueError("invalid attachment filename") from None
    result = {
        "id": expected_id,
        "kind": kind,
        "mime_type": mime,
        "filename": name,
        "size_bytes": size,
        "availability": data["availability"],
    }
    checksum = data.get("sha256")
    if checksum is not None:
        if not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
            raise ValueError("invalid attachment checksum")
        result["sha256"] = checksum
    if data["availability"] == "ready" and (not checksum or size < 1):
        raise ValueError("ready attachment must have content identity")
    if isinstance(data.get("caption"), str):
        result["caption"] = data["caption"][:1024]
    if type(data.get("voice_note")) is bool:
        result["voice_note"] = data["voice_note"]
    if data.get("playback_mime_type") in {"audio/mp4", "audio/ogg"}:
        result["playback_mime_type"] = data["playback_mime_type"]
    error = data.get("error")
    if isinstance(error, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", error):
        result["error"] = error
    return result


def range_headers(headers: Headers) -> dict[str, str]:
    result = {}
    value = headers.get("range")
    if value:
        if len(value) > 80 or not re.fullmatch(r"bytes=(?:[0-9]+-[0-9]*|-[0-9]+)", value):
            raise HTTPException(416, "Only one byte range is supported")
        result["Range"] = value
    conditional = headers.get("if-range")
    if conditional:
        if len(conditional) > 200 or any(ord(c) < 32 for c in conditional):
            raise HTTPException(416, "Invalid range validator")
        result["If-Range"] = conditional
    return result


class BodyLimitMiddleware:
    """Allow large binary bodies only on the dedicated bounded upload route."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        upload = scope.get("method") == "POST" and bool(_UPLOAD_PATH.fullmatch(scope.get("path", "")))
        maximum = MAX_UPLOAD_BYTES if upload else 1024**2
        declared = Headers(scope=scope).get("content-length")
        if declared and (not declared.isdigit() or int(declared) > maximum):
            await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)
            return
        count = 0

        async def bounded_receive() -> Message:
            nonlocal count
            message = await receive()
            if message["type"] == "http.request":
                count += len(message.get("body", b""))
                if count > maximum:
                    raise HTTPException(413, "Request body too large")
            return message

        await self.app(scope, bounded_receive, send)


AsyncReader = Callable[[], Awaitable[bytes]]
