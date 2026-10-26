"""Validate media locally and verify a newly created Chatwoot storage object."""

import hashlib
import ipaddress
import os
import re
import subprocess
import time
from urllib.parse import urljoin, urlsplit

import imageio_ffmpeg
import requests


ASSET_TIMEOUT = (3, 10)
IMAGE_DECODE_TIMEOUT = 10
WHATSAPP_IMAGE_MAX_BYTES = 5 * 1024 * 1024
OUTBOUND_IMAGE_PREPARATION_TIMEOUT = 15
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_asset_session = requests.Session()
# Storage URLs must never inherit API authentication, netrc credentials or retries.
_asset_session.trust_env = False


class MediaValidationError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def storage_error_code(response):
    """Extract a fixed diagnostic code, never customer content or a signed URL.

    Chatwoot can wrap an S3 exception in JSON with HTTP 422, while object stores
    normally return XML with HTTP 403/404. Limit reads of streamed error bodies.
    """
    codes = (
        "SignatureDoesNotMatch", "InvalidAccessKeyId", "ExpiredToken", "InvalidToken",
        "RequestTimeTooSkewed", "RequestExpired", "AuthorizationQueryParametersError",
        "AuthorizationHeaderMalformed", "AccessDenied", "NoSuchBucket", "NoSuchKey",
        "InvalidBucketName", "Forbidden",
    )
    try:
        body = bytearray()
        for chunk in response.iter_content(2048):
            if not isinstance(chunk, bytes):
                return ""
            body.extend(chunk[:8192 - len(body)])
            if len(body) >= 8192:
                break
        text = body.decode("utf-8", errors="replace")
    except (requests.exceptions.RequestException, AttributeError, TypeError, ValueError):
        return ""
    code = next((code for code in codes if re.search(r"\b" + code + r"\b", text)), "")
    if code:
        return code
    # Chatwoot renders e.message, which may omit the SDK class/error code.
    known_messages = {
        "access denied": "AccessDenied",
        "the aws access key id you provided does not exist in our records": "InvalidAccessKeyId",
        "the request signature we calculated does not match the signature you provided": "SignatureDoesNotMatch",
        "the specified bucket does not exist": "NoSuchBucket",
        "the specified key does not exist": "NoSuchKey",
        "the provided token has expired": "ExpiredToken",
    }
    lowered = text.lower()
    return next((code for message, code in known_messages.items() if message in lowered), "")


def get_media_asset(url, **kwargs):
    # Keep deployment proxy/CA settings without enabling requests' netrc lookup.
    kwargs.setdefault("proxies", requests.utils.get_environ_proxies(url))
    kwargs.setdefault("verify", os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("CURL_CA_BUNDLE") or True)
    return _asset_session.get(url, **kwargs)


def content_type(value):
    mime = str(value or "").split(";", 1)[0].strip().lower()
    return {"image/jpg": "image/jpeg", "audio/mp3": "audio/mpeg"}.get(mime, mime)


def media_kind(mime):
    family = content_type(mime).split("/", 1)[0]
    return family if family in {"image", "audio", "video"} else "file"


def prepare_image_for_whatsapp(data, mime):
    """Keep supported small images intact; fit oversized photos to Meta's limit.

    The Chatwoot original remains unchanged. Only the outgoing copy is converted,
    locally, using the ffmpeg binary already bundled for audio. No business state
    or storage configuration is needed in either new or existing deployments.
    """
    if not isinstance(data, bytes) or not data:
        raise MediaValidationError("attachment_image_empty_or_invalid")
    normalized_mime = content_type(mime)
    extension = {"image/jpeg": ".jpg", "image/png": ".png"}.get(normalized_mime)
    if extension is None:
        raise MediaValidationError("attachment_image_format_unsupported")
    if len(data) <= WHATSAPP_IMAGE_MAX_BYTES:
        return data, normalized_mime, extension

    deadline = time.monotonic() + OUTBOUND_IMAGE_PREPARATION_TIMEOUT
    try:
        executable = imageio_ffmpeg.get_ffmpeg_exe()
        # Fix the input decoder so an attachment cannot be interpreted as a
        # playlist that fetches additional resources. Produce one bounded frame.
        decoder = "mjpeg" if normalized_mime == "image/jpeg" else "png"
        for dimension, quality in ((2048, 3), (1280, 5)):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MediaValidationError("attachment_image_prepare_timeout")
            scale = (f"scale=w='min({dimension},iw)':h='min({dimension},ih)'"
                     ":force_original_aspect_ratio=decrease")
            result = subprocess.run(
                [executable, "-nostdin", "-hide_banner", "-loglevel", "error",
                 "-xerror", "-threads", "1", "-f", "image2pipe", "-c:v", decoder,
                 "-i", "pipe:0", "-map", "0:v:0", "-frames:v", "1", "-vf", scale,
                 "-threads", "1", "-codec:v", "mjpeg", "-q:v", str(quality),
                 "-pix_fmt", "yuvj420p", "-f", "image2pipe", "pipe:1"],
                input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=remaining, check=True,
            )
            prepared = result.stdout
            if not prepared or not prepared.startswith(b"\xff\xd8\xff"):
                raise MediaValidationError("attachment_image_prepare_invalid_output")
            if len(prepared) <= WHATSAPP_IMAGE_MAX_BYTES:
                return prepared, "image/jpeg", ".jpg"
    except subprocess.TimeoutExpired as error:
        raise MediaValidationError("attachment_image_prepare_timeout") from error
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        raise MediaValidationError("attachment_image_prepare_failed") from error
    raise MediaValidationError("attachment_image_too_large_after_prepare")


def validate_source_media(data, mime, max_bytes):
    if not isinstance(data, bytes) or not data:
        raise MediaValidationError("source_empty_or_invalid")
    if len(data) > max_bytes:
        raise MediaValidationError("source_too_large")
    if media_kind(mime) != "image":
        return
    signatures = {
        "image/jpeg": lambda value: value.startswith(b"\xff\xd8\xff"),
        "image/png": lambda value: value.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/gif": lambda value: value.startswith((b"GIF87a", b"GIF89a")),
        "image/webp": lambda value: value.startswith(b"RIFF") and value[8:12] == b"WEBP",
        "image/bmp": lambda value: value.startswith(b"BM"),
        "image/tiff": lambda value: value.startswith((b"II*\x00", b"MM\x00*")),
    }
    matches = signatures.get(content_type(mime))
    if matches is None or not matches(data):
        raise MediaValidationError("source_image_signature_mismatch")
    try:
        subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error",
             "-xerror", "-err_detect", "explode", "-i", "pipe:0", "-map", "0:v:0",
             "-frames:v", "1", "-f", "null", "-"],
            input=data, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=IMAGE_DECODE_TIMEOUT, check=True,
        )
    except subprocess.TimeoutExpired as error:
        raise MediaValidationError("source_image_decode_timeout") from error
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        raise MediaValidationError("source_image_decode_failed") from error


def _identifier(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and len(value) <= 20 and value.isascii() and value.isdecimal() and int(value) > 0:
        return int(value)
    return None


def uploaded_attachment(response, *, expected_mime, expected_size, account_id, diagnostics=None):
    try:
        message = response.json()
    except (ValueError, TypeError, AttributeError) as error:
        raise MediaValidationError("upload_invalid_json") from error
    if not isinstance(message, dict) or not _identifier(message.get("id")):
        raise MediaValidationError("upload_missing_message_id")
    if diagnostics is not None:
        diagnostics["message_id"] = _identifier(message["id"])
    if message.get("status") == "failed":
        raise MediaValidationError("upload_message_failed")
    attachments = message.get("attachments")
    if not isinstance(attachments, list) or len(attachments) != 1 or not isinstance(attachments[0], dict):
        raise MediaValidationError("upload_missing_attachment")
    attachment = attachments[0]
    if not _identifier(attachment.get("id")):
        raise MediaValidationError("upload_missing_attachment_id")
    if diagnostics is not None:
        diagnostics["attachment_id"] = _identifier(attachment["id"])
    if _identifier(attachment.get("message_id")) != _identifier(message["id"]):
        raise MediaValidationError("upload_message_id_mismatch")
    if _identifier(attachment.get("account_id")) != _identifier(account_id):
        raise MediaValidationError("upload_account_id_mismatch")
    if attachment.get("file_type") != media_kind(expected_mime):
        raise MediaValidationError("upload_media_kind_mismatch")
    # Chatwoot releases such as v4.4 omit content_type from attachment metadata.
    # Validate it when supplied; the object's HTTP MIME and exact bytes remain
    # mandatory below, including on those older installations.
    if "content_type" in attachment and content_type(attachment["content_type"]) != content_type(expected_mime):
        raise MediaValidationError("upload_content_type_mismatch")
    if type(attachment.get("file_size")) is not int or attachment["file_size"] != expected_size:
        raise MediaValidationError("upload_size_mismatch")
    return _identifier(message["id"]), attachment


def _safe_asset_url(url):
    if not isinstance(url, str):
        raise MediaValidationError("asset_url_missing")
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.fragment):
            raise MediaValidationError("asset_url_unsafe")
        # Accessing port rejects malformed/out-of-range ports; valid custom TLS
        # ports remain supported for self-hosted Chatwoot installations.
        if parsed.port == 0:
            raise MediaValidationError("asset_url_unsafe")
        hostname = parsed.hostname.lower()
        if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
            raise MediaValidationError("asset_url_unsafe")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise MediaValidationError("asset_url_unsafe")
    except ValueError as error:
        if isinstance(error, MediaValidationError):
            raise
        raise MediaValidationError("asset_url_unsafe") from error
    return url


def verify_attachment_asset(url, original, mime, max_bytes, *, fetch=get_media_asset, diagnostics=None):
    """Read back the exact object without API headers or automatic redirects."""
    target = _safe_asset_url(url)
    expected_digest = hashlib.sha256(original).digest()
    for redirects in range(4):
        response = fetch(target, headers={}, stream=True, allow_redirects=False, timeout=ASSET_TIMEOUT)
        try:
            if diagnostics is not None:
                diagnostics.update(asset_status=response.status_code, redirects_followed=redirects)
            if response.status_code in _REDIRECT_STATUSES:
                if redirects == 3:
                    raise MediaValidationError("asset_redirect_limit")
                location = response.headers.get("Location") or response.headers.get("location")
                if not location:
                    raise MediaValidationError("asset_redirect_missing")
                target = _safe_asset_url(urljoin(target, location))
                continue
            if response.status_code != 200:
                if diagnostics is not None:
                    diagnostics["storage_error_code"] = storage_error_code(response)
                raise MediaValidationError("asset_http_error")
            declared = response.headers.get("Content-Length") or response.headers.get("content-length")
            if declared is not None:
                try:
                    declared_size = int(declared)
                except (TypeError, ValueError) as error:
                    raise MediaValidationError("asset_invalid_length") from error
                if declared_size < 0 or declared_size > max_bytes:
                    raise MediaValidationError("asset_too_large")
            received_mime = response.headers.get("Content-Type") or response.headers.get("content-type")
            if content_type(received_mime) != content_type(mime):
                raise MediaValidationError("asset_content_type_mismatch")
            digest = hashlib.sha256()
            size = 0
            for chunk in response.iter_content(64 * 1024):
                if not isinstance(chunk, bytes):
                    raise MediaValidationError("asset_invalid_bytes")
                size += len(chunk)
                if diagnostics is not None:
                    diagnostics["asset_bytes_received"] = size
                if size > max_bytes:
                    raise MediaValidationError("asset_too_large")
                digest.update(chunk)
            if size != len(original) or digest.digest() != expected_digest:
                raise MediaValidationError("asset_bytes_mismatch")
            return
        finally:
            response.close()
