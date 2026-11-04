"""Bot-owned private media archive and Meta upload, never a second message sender.

Only this module knows storage/Meta credentials. All persistent objects live in
this bot's existing private Supabase bucket; local files are bounded temporary
processing buffers. URLs are resolved server-side, never accepted from callers.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote, urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder

from crm_media_policy import (
    LIMITS,
    MAX_UPLOAD_BYTES,
    MIMES,
    MediaError,
    create_playback_audio,
    inspect_media,
    normalized_mime,
    safe_filename,
)
from human_support import uses_crm
from provider_errors import ProviderError, provider_error

CHUNK = 64 * 1024
HTTP_TIMEOUT = (5, 60)
UPLOAD_TIMEOUT = (5, 180)


def media_enabled(config: Any) -> bool:
    configured = str(getattr(config, "CRM_MEDIA_ALLOWED_BUSINESSES", "") or "").strip()
    allowed = {item.strip() for item in configured.split(",") if item.strip()}
    return bool(
        getattr(config, "CRM_MEDIA_ENABLED", False)
        and uses_crm(config)
        and (not allowed or config.BUSINESS_ID in allowed)
    )


def canonical_id(value: str) -> str:
    try:
        parsed = UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise MediaError("invalid_attachment_identity", 422) from None
    if str(parsed) != str(value).lower():
        raise MediaError("invalid_attachment_identity", 422)
    return str(parsed)


def require_human_control(
    store: Any, phone: str, actor: str, version: int
) -> dict[str, Any]:
    snapshot = store.get_control(phone)
    if (
        snapshot.get("phase") != "human"
        or snapshot.get("owner") != actor
        or not snapshot.get("is_paused")
        or not snapshot.get("confirmed")
    ):
        raise MediaError("confirmed_human_control_required", 409)
    if snapshot.get("version") != version:
        raise MediaError("control_version_conflict", 409)
    if not snapshot.get("can_send"):
        raise MediaError("whatsapp_window_or_send_control_unavailable", 409)
    return snapshot


def _phone(value: str) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9_.:+-]{1,200}", value)
        or value in {".", ".."}
    ):
        raise MediaError("invalid_conversation_identity", 422)
    return value


def _content_range(value: str | None, size: int) -> tuple[int, int, bool]:
    def invalid(code: str) -> MediaError:
        error = MediaError(code, 416)
        error.headers = {"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"}
        return error

    if not value:
        return 0, size - 1, False
    matched = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if not matched or not any(matched.groups()):
        raise invalid("invalid_attachment_range")
    first, last = matched.groups()
    if not first:
        amount = int(last)
        if amount < 1:
            raise invalid("invalid_attachment_range")
        start, end = max(0, size - amount), size - 1
    else:
        start, end = int(first), min(int(last) if last else size - 1, size - 1)
    if start >= size or end < start:
        raise invalid("attachment_range_not_satisfiable")
    return start, end, True


def public_attachment(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "business_id": str(row["business_id"]),
        "phone_number": str(row["phone_number"]),
        "kind": row["kind"],
        "mime_type": row["mime_type"],
        "filename": row["filename"],
        "size_bytes": int(row.get("size_bytes") or 0),
        "sha256": row.get("sha256") or None,
        "voice_note": bool(row.get("voice_note")),
        "availability": "ready" if row.get("status") == "ready" else "unavailable",
        **({"playback_mime_type": "audio/mp4"} if row.get("kind") == "audio" else {}),
        **({"error": row["error"]} if row.get("error") else {}),
    }


class MediaService:
    def __init__(self, config: Any, database: Any, *, transport: Any = None):
        self.config, self.database = config, database
        self.http = transport or requests.Session()
        self.business = str(config.BUSINESS_ID)
        self.bucket = str(getattr(config, "CRM_MEDIA_BUCKET", "crm-private-media"))
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", self.bucket):
            raise MediaError("invalid_private_media_bucket", 503)
        origin = urlsplit(str(config.SUPABASE_URL or ""))
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.query
            or origin.fragment
            or origin.path not in {"", "/"}
        ):
            raise MediaError("invalid_private_storage_origin", 503)
        self.storage_origin = f"https://{origin.netloc}"

    def _enabled(self) -> None:
        if not media_enabled(self.config):
            raise MediaError("crm_media_not_enabled", 404)

    def readiness(self) -> dict[str, Any]:
        if not media_enabled(self.config):
            return {
                "business_id": self.business,
                "media_enabled": False,
                "storage_ready": False,
                "reason": "crm_media_not_enabled",
                "limits": LIMITS,
            }
        try:
            self._table().select("id").eq("business_id", self.business).limit(
                1
            ).execute()
            self._check_private_bucket()
        except Exception as error:
            return {
                "business_id": self.business,
                "media_enabled": True,
                "storage_ready": False,
                "reason": error.code
                if isinstance(error, MediaError)
                else "private_media_schema_unavailable",
                "limits": LIMITS,
            }
        return {
            "business_id": self.business,
            "media_enabled": True,
            "storage_ready": True,
            "reason": None,
            "limits": LIMITS,
            "mime_types": [mime for mime in MIMES if mime != "audio/webm"],
            "recording_input_mime_types": ["audio/webm", "audio/mp4", "audio/ogg"],
        }

    def _table(self) -> Any:
        return self.database.table("crm_media_assets")

    def _get(self, phone: str, asset_id: str) -> dict[str, Any]:
        self._enabled()
        phone, asset_id = _phone(phone), canonical_id(asset_id)
        try:
            data = (
                self._table()
                .select("*")
                .eq("business_id", self.business)
                .eq("phone_number", phone)
                .eq("id", asset_id)
                .limit(1)
                .execute()
                .data
            ) or []
        except Exception:
            raise MediaError("private_media_schema_unavailable", 503) from None
        if not data:
            raise MediaError("attachment_not_found", 404)
        return dict(data[0])

    def _save(self, row: dict[str, Any], *, insert: bool = False) -> None:
        try:
            if insert:
                self._table().insert(row).execute()
            else:
                (
                    self._table()
                    .update(row)
                    .eq("business_id", self.business)
                    .eq("phone_number", row["phone_number"])
                    .eq("id", row["id"])
                    .execute()
                )
        except Exception:
            raise MediaError("private_media_persistence_unavailable", 503) from None

    def _storage_headers(self) -> dict[str, str]:
        key = str(self.config.SUPABASE_KEY or "")
        return {"Authorization": "Bearer " + key, "apikey": key}

    def _check_private_bucket(self) -> None:
        response = self.http.get(
            self.storage_origin + "/storage/v1/bucket/" + quote(self.bucket, safe=""),
            headers=self._storage_headers(),
            timeout=HTTP_TIMEOUT,
            allow_redirects=False,
        )
        try:
            if response.status_code != 200:
                raise MediaError("private_media_storage_not_configured", 503)
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("public") is not False:
                raise MediaError("media_bucket_must_be_private", 503)
        finally:
            response.close()

    def _key(
        self, phone: str, asset_id: str, digest: str, suffix: str = "original"
    ) -> str:
        return f"{self.business}/{hashlib.sha256(phone.encode()).hexdigest()}/{asset_id}/{digest}/{suffix}"

    def _url(self, key: str, *, authenticated: bool = False) -> str:
        prefix = (
            "/storage/v1/object/authenticated/"
            if authenticated
            else "/storage/v1/object/"
        )
        return (
            self.storage_origin
            + prefix
            + quote(self.bucket, safe="")
            + "/"
            + quote(key, safe="/")
        )

    def _upload_private(self, stream: BinaryIO, key: str, mime: str, size: int) -> None:
        self._check_private_bucket()
        stream.seek(0)
        headers = {
            **self._storage_headers(),
            "Content-Type": mime,
            "Content-Length": str(size),
            "x-upsert": "false",
        }
        response = self.http.post(
            self._url(key),
            headers=headers,
            data=stream,
            timeout=UPLOAD_TIMEOUT,
            allow_redirects=False,
        )
        try:
            if response.status_code not in {200, 201, 400, 409}:
                raise MediaError("private_media_upload_failed", 503)
            if response.status_code in {400, 409}:
                # A lost upload acknowledgement may leave the immutable hash-keyed
                # object present. HEAD checks existence/size without overwriting it.
                head = self.http.head(
                    self._url(key, authenticated=True),
                    headers=self._storage_headers(),
                    timeout=HTTP_TIMEOUT,
                    allow_redirects=False,
                )
                try:
                    if (
                        head.status_code != 200
                        or int(head.headers.get("Content-Length", "-1")) != size
                    ):
                        raise MediaError("private_media_upload_failed", 503)
                finally:
                    head.close()
        finally:
            response.close()

    def upload(
        self,
        phone: str,
        asset_id: str,
        actor: str,
        path: str,
        mime: str,
        filename: str,
        *,
        voice_note: bool = False,
    ) -> dict[str, Any]:
        self._enabled()
        phone, asset_id = _phone(phone), canonical_id(asset_id)
        prepared = inspect_media(path, mime, filename, voice_note=voice_note)
        original_path = path
        try:
            try:
                existing = self._get(phone, asset_id)
            except MediaError as error:
                if error.status_code != 404:
                    raise
                existing = None
            if existing:
                if (
                    existing.get("actor") != actor
                    or existing.get("source_sha256") != self._digest_path(original_path)
                    or bool(existing.get("voice_note")) != bool(voice_note)
                    or existing.get("source_filename") != safe_filename(filename)
                    or existing.get("source_mime_type") != normalized_mime(mime)
                ):
                    raise MediaError("attachment_upload_identity_conflict", 409)
                if existing.get("status") == "ready":
                    return public_attachment(existing)
            row = {
                "business_id": self.business,
                "id": asset_id,
                "phone_number": phone,
                "actor": actor,
                "direction": "outbound",
                "kind": prepared.kind,
                "mime_type": prepared.mime_type,
                "filename": prepared.filename,
                "size_bytes": prepared.size_bytes,
                "sha256": prepared.sha256,
                "source_sha256": self._digest_path(original_path),
                "source_filename": safe_filename(filename),
                "source_mime_type": normalized_mime(mime),
                "voice_note": bool(voice_note),
                "status": "pending",
                "storage_key": self._key(phone, asset_id, prepared.sha256),
                "original_storage_key": None,
                "source_event_id": None,
                "meta_media_id": None,
                "error": None,
            }
            if existing is None:
                self._save(row, insert=True)
            try:
                with open(prepared.path, "rb") as stream:
                    self._upload_private(
                        stream,
                        row["storage_key"],
                        prepared.mime_type,
                        prepared.size_bytes,
                    )
                if prepared.path != original_path:
                    source_key = self._key(
                        phone, asset_id, row["source_sha256"], "recording-original"
                    )
                    with open(original_path, "rb") as stream:
                        self._upload_private(
                            stream,
                            source_key,
                            normalized_mime(mime),
                            Path(original_path).stat().st_size,
                        )
                    row["original_storage_key"] = source_key
                row["status"] = "ready"
                self._save(row)
                return public_attachment(row)
            except Exception:
                row["status"], row["error"] = (
                    "unavailable",
                    "private_media_upload_failed",
                )
                self._save(row)
                raise MediaError("private_media_upload_failed", 503) from None
        finally:
            if prepared.path != original_path:
                Path(prepared.path).unlink(missing_ok=True)

    @staticmethod
    def _digest_path(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            while chunk := stream.read(CHUNK):
                digest.update(chunk)
        return digest.hexdigest()

    def metadata(self, phone: str, asset_id: str) -> dict[str, Any]:
        return public_attachment(self._get(phone, asset_id))

    def outbound(self, phone: str, asset_id: str, actor: str) -> dict[str, Any]:
        row = self._get(phone, asset_id)
        if row.get("direction") != "outbound" or row.get("actor") != actor:
            raise MediaError("attachment_not_owned_by_operator", 403)
        if row.get("status") != "ready" or not row.get("sha256"):
            raise MediaError("attachment_not_ready", 409)
        return row

    def _meta_download(
        self, media_id: str, target: BinaryIO, maximum: int
    ) -> tuple[str, str]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,300}", str(media_id)):
            raise MediaError("invalid_provider_media_identity")
        headers = {"Authorization": "Bearer " + self.config.WA_TOKEN}
        # A short-lived provider URL may expire between metadata and download.
        # Resolve it again once; this is a GET-only retry, never a message send.
        for attempt in range(2):
            response = self.http.get(
                f"https://graph.facebook.com/v20.0/{media_id}",
                headers=headers,
                timeout=HTTP_TIMEOUT,
                allow_redirects=False,
            )
            try:
                if response.status_code != 200:
                    raise MediaError("provider_media_expired_or_unavailable", 410)
                data = response.json()
                if not isinstance(data, dict):
                    raise MediaError("invalid_provider_media_response", 502)
                if int(data.get("file_size") or 0) > maximum:
                    raise MediaError("attachment_too_large", 413)
            except (ValueError, TypeError):
                raise MediaError("invalid_provider_media_response", 502) from None
            finally:
                response.close()
            url = urlsplit(str(data.get("url") or ""))
            host = url.hostname or ""
            allowed_host = host in {"graph.facebook.com", "lookaside.fbsbx.com"} or any(
                host.endswith(suffix)
                for suffix in (".fbsbx.com", ".fbcdn.net", ".facebook.com")
            )
            if (
                url.scheme != "https"
                or url.port not in {None, 443}
                or url.username
                or url.password
                or not allowed_host
            ):
                raise MediaError("provider_media_origin_rejected", 502)
            response = self.http.get(
                url.geturl(),
                headers=headers,
                stream=True,
                timeout=HTTP_TIMEOUT,
                allow_redirects=False,
            )
            try:
                if response.status_code in {401, 403, 404, 410} and attempt == 0:
                    continue
                if response.status_code != 200:
                    raise MediaError("provider_media_expired_or_unavailable", 410)
                declared = int(response.headers.get("Content-Length", "0"))
                if declared < 0 or declared > maximum:
                    raise MediaError("attachment_too_large", 413)
                target.seek(0)
                target.truncate()
                size = 0
                digest = hashlib.sha256()
                for chunk in response.iter_content(CHUNK):
                    size += len(chunk)
                    if size > maximum:
                        raise MediaError("attachment_too_large", 413)
                    target.write(chunk)
                    digest.update(chunk)
                if size < 1:
                    raise MediaError("empty_attachment")
                provider_digest = str(data.get("sha256") or "")
                if provider_digest:
                    import base64

                    try:
                        expected = (
                            bytes.fromhex(provider_digest)
                            if re.fullmatch(r"[0-9a-fA-F]{64}", provider_digest)
                            else base64.b64decode(provider_digest, validate=True)
                        )
                    except ValueError:
                        raise MediaError("invalid_provider_media_digest", 502) from None
                    if len(expected) != 32 or digest.digest() != expected:
                        raise MediaError("provider_media_digest_mismatch", 502)
                target.seek(0)
                return normalized_mime(
                    data.get("mime_type") or response.headers.get("Content-Type")
                ), provider_digest
            finally:
                response.close()
        raise MediaError("provider_media_expired_or_unavailable", 410)

    def capture_inbound(
        self,
        phone: str,
        source_event_id: str,
        media_id: str,
        mime: str,
        filename: str = "",
        kind: str = "document",
    ) -> dict[str, Any] | None:
        if not media_enabled(self.config):
            return None
        phone = _phone(phone)
        asset_id = str(
            uuid5(
                NAMESPACE_URL,
                json.dumps([self.business, phone, source_event_id, media_id]),
            )
        )
        try:
            existing = self._get(phone, asset_id)
            if existing.get("status") != "ready":
                return public_attachment(self._recover(existing))
            return public_attachment(existing)
        except MediaError as error:
            if error.status_code != 404:
                # No migration/storage must not break the bot's inbound processing.
                return {
                    "id": asset_id,
                    "kind": kind,
                    "mime_type": normalized_mime(mime),
                    "filename": "archivo",
                    "size_bytes": 0,
                    "availability": "unavailable",
                    "error": error.code,
                }
        mime = normalized_mime(mime)
        row = {
            "business_id": self.business,
            "id": asset_id,
            "phone_number": phone,
            "actor": "",
            "direction": "inbound",
            "kind": kind if kind in LIMITS else "document",
            "mime_type": mime or "application/octet-stream",
            "filename": "archivo",
            "size_bytes": 0,
            "sha256": None,
            "source_sha256": None,
            "voice_note": kind == "audio",
            "status": "pending",
            "storage_key": None,
            "original_storage_key": None,
            "source_event_id": source_event_id,
            "meta_media_id": media_id,
            "error": None,
        }
        try:
            row["filename"] = (
                safe_filename(filename)
                if filename
                else "archivo" + MIMES.get(mime, ("document", ".bin"))[1]
            )
            self._save(row, insert=True)
            return public_attachment(self._recover(row))
        except Exception as error:
            row["status"], row["error"] = (
                "unavailable",
                error.code
                if isinstance(error, MediaError)
                else "incoming_media_archive_failed",
            )
            try:
                self._save(row)
            except Exception:
                pass
            return public_attachment(row)

    def _recover(self, row: dict[str, Any]) -> dict[str, Any]:
        if not row.get("meta_media_id"):
            raise MediaError("attachment_archive_unavailable", 410)
        with tempfile.NamedTemporaryFile(mode="w+b") as downloaded:
            mime, _provider_digest = self._meta_download(
                row["meta_media_id"],
                downloaded,
                LIMITS.get(row["kind"], MAX_UPLOAD_BYTES),
            )
            downloaded.flush()
            prepared = inspect_media(
                downloaded.name, mime, row["filename"], normalize=False
            )
            row.update(
                kind=prepared.kind,
                mime_type=prepared.mime_type,
                filename=prepared.filename,
                size_bytes=prepared.size_bytes,
                sha256=prepared.sha256,
                source_sha256=prepared.sha256,
            )
            row["storage_key"] = self._key(
                row["phone_number"], row["id"], prepared.sha256
            )
            downloaded.seek(0)
            self._upload_private(
                downloaded, row["storage_key"], mime, prepared.size_bytes
            )
            row["status"], row["error"] = "ready", None
            self._save(row)
        return row

    def content(
        self,
        phone: str,
        asset_id: str,
        *,
        range_header: str | None = None,
        if_range: str | None = None,
        variant: str = "original",
    ) -> tuple[Iterator[bytes], int, dict[str, str]]:
        row = self._get(phone, asset_id)
        if row.get("status") != "ready":
            row = self._recover(row)
        if variant not in {"original", "playback"}:
            raise MediaError("invalid_attachment_variant")
        if (
            variant == "playback"
            and row["kind"] == "audio"
            and row["mime_type"] != "audio/mp4"
        ):
            row = self._playback(row)
        elif variant == "playback" and row["kind"] != "audio":
            raise MediaError("playback_variant_requires_audio")
        try:
            return self._stream(row, range_header=range_header, if_range=if_range)
        except MediaError as error:
            if (
                error.status_code != 410
                or not row.get("meta_media_id")
                or variant != "original"
            ):
                raise
            # An original provider ID can renew an absent private archive; never
            # retain a temporary Meta URL as the permanent download target.
            return self._stream(
                self._recover(row), range_header=range_header, if_range=if_range
            )

    def _stream(
        self, row: dict[str, Any], *, range_header: str | None, if_range: str | None
    ) -> tuple[Iterator[bytes], int, dict[str, str]]:
        size = int(row["size_bytes"])
        etag = '"' + str(row["sha256"]) + '"'
        selected = range_header if not if_range or if_range == etag else None
        start, end, partial = _content_range(selected, size)
        headers = self._storage_headers()
        if partial:
            headers["Range"] = f"bytes={start}-{end}"
        response = self.http.get(
            self._url(row["storage_key"], authenticated=True),
            headers=headers,
            stream=True,
            timeout=HTTP_TIMEOUT,
            allow_redirects=False,
        )
        if response.status_code not in {200, 206}:
            response.close()
            raise MediaError(
                "attachment_archive_unavailable",
                410 if response.status_code == 404 else 503,
            )
        if (
            response.status_code == 206
            and response.headers.get("Content-Range") != f"bytes {start}-{end}/{size}"
        ):
            response.close()
            raise MediaError("attachment_archive_range_mismatch", 502)
        output = {
            "Content-Type": row["mime_type"],
            "Content-Length": str(end - start + 1),
            "Accept-Ranges": "bytes",
            "ETag": etag,
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Disposition": (
                "attachment"
                if row["mime_type"] == "application/octet-stream"
                else "inline"
            )
            + "; filename=attachment; filename*=UTF-8''"
            + quote(row["filename"], safe=""),
        }
        if partial:
            output["Content-Range"] = f"bytes {start}-{end}/{size}"

        def chunks() -> Iterator[bytes]:
            skip = start if partial and response.status_code == 200 else 0
            remaining = end - start + 1
            try:
                for chunk in response.iter_content(CHUNK):
                    if skip:
                        ignored = min(skip, len(chunk))
                        skip -= ignored
                        chunk = chunk[ignored:]
                    if chunk and remaining:
                        selected_chunk = chunk[:remaining]
                        remaining -= len(selected_chunk)
                        yield selected_chunk
                    if remaining == 0:
                        break
                if remaining:
                    raise MediaError("attachment_archive_truncated", 502)
            finally:
                response.close()

        return chunks(), 206 if partial else 200, output

    def _playback(self, row: dict[str, Any]) -> dict[str, Any]:
        cached = row.get("playback")
        if isinstance(cached, dict) and all(
            cached.get(name)
            for name in ("storage_key", "sha256", "size_bytes", "mime_type", "filename")
        ):
            return {**row, **cached}
        with tempfile.TemporaryDirectory(prefix="crm-playback-") as directory:
            source, target = (
                Path(directory) / "source",
                Path(directory) / "playback.m4a",
            )
            iterator, _, _ = self._stream(row, range_header=None, if_range=None)
            with source.open("wb") as stream:
                for chunk in iterator:
                    stream.write(chunk)
            prepared = create_playback_audio(str(source), str(target), row["mime_type"])
            key = self._key(
                row["phone_number"], row["id"], prepared.sha256, "playback.m4a"
            )
            with target.open("rb") as stream:
                self._upload_private(
                    stream, key, prepared.mime_type, prepared.size_bytes
                )
            row["playback"] = {
                "storage_key": key,
                "sha256": prepared.sha256,
                "size_bytes": prepared.size_bytes,
                "mime_type": prepared.mime_type,
                "filename": prepared.filename,
            }
            self._save(row)
            return {**row, **row["playback"]}

    def send_attachment(
        self, phone: str, row: dict[str, Any], caption: str, sender: Any
    ) -> bool:
        """Upload file to Meta once, then invoke the bot's existing guarded sender."""
        iterator, _, _ = self._stream(row, range_header=None, if_range=None)
        with tempfile.TemporaryFile(mode="w+b") as stream:
            for chunk in iterator:
                stream.write(chunk)
            stream.seek(0)
            multipart = MultipartEncoder(
                fields={
                    "messaging_product": "whatsapp",
                    "file": (row["filename"], stream, row["mime_type"]),
                }
            )
            headers = {
                "Authorization": "Bearer " + self.config.WA_TOKEN,
                "Content-Type": multipart.content_type,
                "Content-Length": str(multipart.len),
            }
            try:
                response = self.http.post(
                    f"https://graph.facebook.com/v20.0/{self.config.WA_PHONE_NUMBER_ID}/media",
                    headers=headers,
                    data=multipart,
                    timeout=UPLOAD_TIMEOUT,
                    allow_redirects=False,
                )
                try:
                    if response.status_code not in {200, 201}:
                        failure = provider_error(
                            "meta", "upload_media", response, (self.config.WA_TOKEN,)
                        )
                        # The media-upload operation never sends a message.
                        failure.confirmed_not_sent = True
                        raise failure
                    try:
                        provider_id = response.json().get("id")
                    except (AttributeError, ValueError, TypeError) as error:
                        raise ProviderError(
                            "meta",
                            "upload_media",
                            None,
                            message="upload acknowledgement invalid",
                            confirmed_not_sent=True,
                        ) from error
                    if not provider_id:
                        raise ProviderError(
                            "meta",
                            "upload_media",
                            None,
                            message="upload acknowledgement missing",
                            confirmed_not_sent=True,
                        )
                finally:
                    response.close()
            except requests.RequestException as error:
                raise ProviderError(
                    "meta",
                    "upload_media",
                    None,
                    message="media upload failed before message send",
                    confirmed_not_sent=True,
                ) from error
            # No retry after this call. The existing sender attaches command callback
            # correlation and checks Meta window + reserved authoritative control.
            return bool(
                sender(
                    phone,
                    str(provider_id),
                    row["kind"],
                    caption or None,
                    row["filename"],
                )
            )
