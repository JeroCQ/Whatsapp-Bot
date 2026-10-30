"""Verify uploaded objects without treating HTTP success as readable media."""

import importlib.util
from pathlib import Path
import struct
import subprocess
import sys
import types
from unittest.mock import patch
import zlib

import pytest
import requests
import imageio_ffmpeg


def png_image(pixel=b"\xff\x00\x00"):
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" + pixel)) + chunk(b"IEND", b""))


class Response:
    def __init__(self, status=200, *, body=None, data=b"", headers=None, invalid_json=False):
        self.status_code = status
        self.body = body
        self.data = data
        self.headers = headers or {}
        self.invalid_json = invalid_json
        self.closed = False
        self.read = False

    def json(self):
        if self.invalid_json:
            raise ValueError("not JSON")
        return self.body

    def iter_content(self, chunk_size):
        self.read = True
        yield self.data

    def close(self):
        self.closed = True


@pytest.fixture
def media_api(monkeypatch):
    settings = types.SimpleNamespace(
        CHATWOOT_BASE_URL="https://chatwoot.example", CHATWOOT_ACCOUNT_ID="10",
        CHATWOOT_API_TOKEN="private-api-token", WA_TOKEN="private-wa-token",
        CHATWOOT_MAX_ATTACHMENT_BYTES=1024 * 1024,
    )
    private_config = types.ModuleType("config")
    private_config.config = settings
    spec = importlib.util.spec_from_file_location(
        "_chatwoot_media_test_api", Path(__file__).resolve().parents[1] / "chatwoot_api.py",
    )
    api = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"config": private_config}):
        spec.loader.exec_module(api)
    image = png_image()
    attachment = {
        "id": 31, "message_id": 22, "account_id": 10, "file_type": "image",
        "content_type": "image/png", "file_size": len(image),
        "data_url": "https://chatwoot.example/rails/storage/image.png?signature=private-signature",
    }
    uploaded = Response(body={"id": 22, "attachments": [attachment]})
    asset = Response(data=image, headers={"Content-Type": "image/png", "Content-Length": str(len(image))})
    calls = {"post": [], "get": []}
    asset_responses = [asset]

    def post(url, **kwargs):
        calls["post"].append((url, kwargs))
        return uploaded

    def get(url, **kwargs):
        calls["get"].append((url, kwargs))
        return asset_responses.pop(0)

    monkeypatch.setattr(api, "post", post)
    monkeypatch.setattr(api, "get_media_asset", get)
    return types.SimpleNamespace(api=api, image=image, attachment=attachment, uploaded=uploaded,
        asset=asset, asset_responses=asset_responses, calls=calls)


def send_image(context):
    return context.api.send_media_to_chatwoot(44, "Imagen de prueba", context.image, "image/png", "test.png")


def test_audio_conversion_cannot_wait_forever_or_expose_raw_decoder_errors(media_api, monkeypatch):
    f = media_api
    def stalled(command, **kwargs):
        assert kwargs["timeout"] == 15
        raise subprocess.TimeoutExpired(command, 15, stderr=b"PRIVATE_DECODER_OUTPUT")
    monkeypatch.setattr(f.api.subprocess, "run", stalled)
    with pytest.raises(f.api.MediaValidationError, match="attachment_audio_prepare_timeout") as caught:
        f.api.prepare_audio_for_delivery(b"synthetic-wav", "audio/wav")
    assert "PRIVATE_DECODER_OUTPUT" not in str(caught.value)


def test_m4a_alias_preserves_bytes_without_conversion_and_uses_meta_mime(media_api, monkeypatch):
    monkeypatch.setattr(media_api.api.subprocess, "run", lambda *a, **k: pytest.fail("compatible M4A needs no conversion"))
    original = b"synthetic-m4a"
    assert media_api.api.prepare_audio_for_delivery(original, "audio/x-m4a") == (original, "audio/mp4", ".m4a")


@pytest.mark.parametrize("status", [200, 201])
def test_valid_image_requires_identical_readable_storage_object(media_api, status):
    f = media_api
    f.uploaded.status_code = status

    assert send_image(f) is f.uploaded

    assert len(f.calls["post"]) == 1
    assert f.calls["get"][0][1] == {
        "headers": {}, "stream": True, "allow_redirects": False, "timeout": (3, 10),
    }
    assert f.asset.closed


def test_jpeg_is_relayed_unchanged_and_verified_by_readback(media_api):
    f = media_api
    jpeg = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-frames:v", "1", "-codec:v", "mjpeg", "-f", "image2pipe", "pipe:1"],
        input=f.image, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=10,
    ).stdout
    f.attachment.update(content_type="image/jpeg", file_size=len(jpeg))
    f.asset.data = jpeg
    f.asset.headers = {"Content-Type": "image/jpeg", "Content-Length": str(len(jpeg))}

    assert f.api.send_media_to_chatwoot(44, "Imagen de prueba", jpeg, "image/jpeg", "test.jpg") is f.uploaded
    assert f.asset.closed


def webp_image(*, animated=False):
    """Generate valid WebP bytes locally; relay tests must never decode them."""
    frames = png_image() + (png_image(b"\x00\xff\x00") if animated else b"")
    return subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
         "-f", "image2pipe", "-framerate", "2", "-c:v", "png", "-i", "pipe:0",
         "-frames:v", "2" if animated else "1", "-c:v", "libwebp_anim" if animated else "libwebp",
         "-f", "webp", "pipe:1"],
        input=frames, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=10,
    ).stdout


@pytest.mark.parametrize("business,account", [("tanaka", 10), ("memos", 20), ("velvet", 30), ("future-brand", 40)])
@pytest.mark.parametrize("kind", ["static_webp", "animated_webp", "pdf", "office", "unknown_image", "binary"])
def test_meta_media_relay_preserves_every_payload_without_a_decoder_or_format_allowlist(
        media_api, monkeypatch, business, account, kind):
    f = media_api
    fixtures = {
        "pdf": (b"%PDF-1.7\nSynthetic customer document\n%%EOF", "application/pdf", "portfolio.pdf"),
        "office": (b"PK\x03\x04Synthetic workbook bytes", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "order.xlsx"),
        "unknown_image": (b"Synthetic future image format", "image/future-format", "future.img"),
        "binary": (b"\x00\x01\xffSynthetic arbitrary bytes", "application/octet-stream", "original.bin"),
    }
    if kind.endswith("webp"):
        source = webp_image(animated=kind == "animated_webp")
        if kind == "animated_webp":
            assert b"ANIM" in source
        mime, filename = "image/webp", "sticker.webp"
    else:
        source, mime, filename = fixtures[kind]
    f.api.config.BUSINESS_ID = business
    f.api.config.CHATWOOT_ACCOUNT_ID = str(account)
    # Chatwoot is permitted to classify an image as a generic downloadable file.
    f.attachment.update(account_id=account, content_type=mime, file_type="file", file_size=len(source))
    f.asset.data = source
    f.asset.headers = {"Content-Type": "application/octet-stream", "Content-Length": str(len(source))}
    posted = []

    def post(url, **kwargs):
        posted.append(kwargs)
        assert f"/accounts/{account}/" in url
        assert "files" not in kwargs
        encoder = kwargs["data"]
        assert isinstance(encoder, f.api.MultipartEncoder)
        assert encoder.fields["message_type"] == "incoming"
        assert encoder.fields["private"] == "false"
        name, handle, sent_mime = encoder.fields["attachments[]"]
        assert (name, sent_mime) == (filename, mime)
        assert not handle.closed
        assert source in encoder.to_string()
        return f.uploaded

    monkeypatch.setattr(f.api, "post", post)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("incoming relay must not decode or transform media"))
    assert f.api.send_media_to_chatwoot(44, "Archivo de prueba", source, mime, filename) is f.uploaded
    assert len(posted) == 1
    assert posted[0]["data"].fields["attachments[]"][1].closed
    assert f.asset.closed and f.asset.read


def test_large_incoming_document_streams_multipart_without_building_a_second_complete_body(media_api, monkeypatch):
    import hashlib

    f = media_api
    source = b"%PDF-1.7\n" + b"0" * (26 * 1024 * 1024)
    f.api.config.CHATWOOT_MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024
    f.attachment.update(content_type="application/pdf", file_type="file", file_size=len(source))
    f.asset.data = source
    f.asset.headers = {"Content-Type": "application/pdf", "Content-Length": str(len(source))}
    posted = []

    def post(url, **kwargs):
        posted.append(kwargs)
        assert "files" not in kwargs
        encoder = kwargs["data"]
        handle = encoder.fields["attachments[]"][1]
        actual = hashlib.sha256()
        while chunk := handle.read(64 * 1024):
            actual.update(chunk)
        assert actual.digest() == hashlib.sha256(source).digest()
        handle.seek(0)
        prepared = requests.Request("POST", url, headers=kwargs["headers"], data=encoder).prepare()
        assert prepared.body is encoder
        count = 0
        while chunk := encoder.read(64 * 1024):
            assert len(chunk) <= 64 * 1024
            count += len(chunk)
        assert count == encoder.len == int(kwargs["headers"]["Content-Length"])
        return f.uploaded

    monkeypatch.setattr(f.api, "post", post)
    assert f.api.send_media_to_chatwoot(44, "PDF de prueba", source, "application/pdf", "portfolio.pdf") is f.uploaded
    assert len(posted) == 1 and posted[0]["data"].fields["attachments[]"][1].closed


@pytest.mark.parametrize("field,value,diagnostic", [
    ("file_type", "file", "metadata_kind_mismatch"),
    ("content_type", "application/octet-stream", "metadata_mime_mismatch"),
    ("file_size", 1, "metadata_size_mismatch"),
    ("file_size", None, "metadata_size_mismatch"),
])
def test_classification_mismatch_is_diagnostic_when_original_bytes_are_verified(media_api, capsys, field, value, diagnostic):
    f = media_api
    f.attachment[field] = value
    assert send_image(f) is f.uploaded
    assert f.asset.read and f.asset.closed
    assert f'"{diagnostic}": true' in capsys.readouterr().out


@pytest.mark.parametrize("reported_size", [None, 1])
def test_missing_or_wrong_metadata_size_never_bypasses_actual_byte_verification(media_api, capsys, reported_size):
    f = media_api
    f.attachment["file_size"] = reported_size
    f.asset.data = b"not the customer's uploaded bytes"
    assert send_image(f) is None
    assert f.asset.read and f.asset.closed
    assert "asset_bytes_mismatch" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["transport", "provider_rejection"])
def test_streamed_incoming_payload_is_closed_on_upload_failure_without_retries(media_api, monkeypatch, failure):
    f = media_api
    payloads = []
    def post(url, **kwargs):
        payloads.append(kwargs["data"])
        if failure == "transport":
            raise requests.ReadTimeout("private signed-url must not be logged")
        f.uploaded.status_code = 422
        return f.uploaded
    monkeypatch.setattr(f.api, "post", post)
    assert send_image(f) is None
    assert len(payloads) == 1
    assert payloads[0].fields["attachments[]"][1].closed
    assert f.calls["get"] == []


@pytest.mark.parametrize("received_mime", [None, "application/octet-stream", "text/html"])
def test_exact_readback_bytes_are_authoritative_when_storage_mime_differs(media_api, capsys, received_mime):
    f = media_api
    if received_mime:
        f.asset.headers["Content-Type"] = received_mime
    else:
        f.asset.headers.pop("Content-Type")
    assert send_image(f) is f.uploaded
    assert f.asset.read and f.asset.closed
    assert '"asset_mime_mismatch": true' in capsys.readouterr().out


@pytest.mark.parametrize("business,account", [("tanaka", 10), ("memos", 20), ("velvet", 30), ("future-brand", 40)])
def test_chatwoot_44_response_without_content_type_still_verifies_real_object(media_api, business, account):
    f = media_api
    f.api.config.BUSINESS_ID = business
    f.api.config.CHATWOOT_ACCOUNT_ID = str(account)
    f.attachment["account_id"] = account
    # Actual v4.4 Attachment#file_metadata contract, rather than develop's MIME.
    f.attachment.pop("content_type")
    f.attachment.update(extension="png", thumb_url="", width=None, height=None)
    assert send_image(f) is f.uploaded
    assert f.asset.read and f.asset.closed


def test_legacy_metadata_keeps_account_isolation(media_api):
    f = media_api
    f.attachment.pop("content_type")
    f.attachment["account_id"] = 99
    assert send_image(f) is None
    assert not f.calls["get"]


def test_legacy_metadata_never_bypasses_actual_bytes_verification(media_api):
    f = media_api
    f.attachment.pop("content_type")
    f.asset.data = b"<html>not the uploaded object</html>"
    assert send_image(f) is None
    assert len(f.calls["post"]) == 1
    assert f.asset.closed


def test_storage_exception_wrapped_in_422_is_diagnosed_without_resubmission(media_api, capsys):
    f = media_api
    f.uploaded.status_code = 422
    f.uploaded.data = b'{"error":"Aws::S3::Errors::AccessDenied PRIVATE_CUSTOMER https://storage.example/?signature=SECRET"}'
    assert send_image(f) is None
    assert len(f.calls["post"]) == 1 and not f.calls["get"]
    output = capsys.readouterr().out
    assert '"storage_error_code": "AccessDenied"' in output
    assert "PRIVATE_CUSTOMER" not in output and "SECRET" not in output


def test_chatwoot_exception_message_without_sdk_class_is_classified(media_api, capsys):
    f = media_api
    f.uploaded.status_code = 422
    f.uploaded.headers["X-Request-Id"] = "e9a6cdb0-6ab3-435c-a035-000000000001"
    f.uploaded.data = b'{"error":"Access Denied"}'
    assert send_image(f) is None
    output = capsys.readouterr().out
    assert '"storage_error_code": "AccessDenied"' in output
    assert '"provider_request_id": "e9a6cdb0-6ab3-435c-a035-000000000001"' in output
    assert f.uploaded.closed and len(f.calls["post"]) == 1


def test_storage_xml_403_exposes_only_known_code_and_closes_object(media_api, capsys):
    f = media_api
    f.asset.status_code = 403
    f.asset.data = b'<Error><Code>SignatureDoesNotMatch</Code><Message>PRIVATE_SIGNED_URL</Message></Error>'
    assert send_image(f) is None
    output = capsys.readouterr().out
    assert '"storage_error_code": "SignatureDoesNotMatch"' in output
    assert "PRIVATE_SIGNED_URL" not in output
    assert f.asset.closed and len(f.calls["post"]) == 1


def test_storage_error_body_timeout_preserves_http_failure_without_resubmitting(media_api, capsys):
    f = media_api
    f.asset.status_code = 403

    def broken_body(_size):
        raise requests.ReadTimeout("PRIVATE_SIGNED_URL")
        yield  # Make the failure happen during streaming rather than before it.

    f.asset.iter_content = broken_body
    assert send_image(f) is None
    output = capsys.readouterr().out
    assert '"asset_status": 403' in output and '"storage_error_code": ""' in output
    assert "PRIVATE_SIGNED_URL" not in output
    assert f.asset.closed and len(f.calls["post"]) == 1


def test_storage_error_diagnostic_bounds_stream_and_never_echoes_unknown_error():
    from chatwoot_media import storage_error_code

    class LargeError:
        def iter_content(self, _size):
            yield b"private-unrecognized-response" * 400
            pytest.fail("error body must not be read past the diagnostic limit")

    assert storage_error_code(LargeError()) == ""


@pytest.mark.parametrize("invalid_image", [b"", None, "not bytes"])
def test_empty_or_non_byte_source_is_not_uploaded(media_api, invalid_image):
    f = media_api
    f.image = invalid_image
    assert send_image(f) is None
    assert f.calls == {"post": [], "get": []}


def test_source_over_configured_limit_is_rejected_before_post(media_api):
    f = media_api
    f.api.config.CHATWOOT_MAX_ATTACHMENT_BYTES = len(f.image) - 1
    assert send_image(f) is None
    assert f.calls["post"] == []


@pytest.mark.parametrize("bad_reply,reason", [
    ("html", "upload_invalid_json"),
    ({}, "upload_missing_message_id"),
    ({"id": 22}, "upload_missing_attachment"),
    ({"id": 22, "attachments": []}, "upload_missing_attachment"),
    ({"id": 22, "attachments": [{}]}, "upload_missing_attachment_id"),
])
def test_http_200_without_a_valid_attachment_is_not_success(media_api, capsys, bad_reply, reason):
    f = media_api
    f.uploaded.invalid_json = bad_reply == "html"
    f.uploaded.body = bad_reply
    assert send_image(f) is None
    assert len(f.calls["post"]) == 1
    assert f.calls["get"] == []
    assert reason in capsys.readouterr().out


def test_failed_message_status_is_not_a_verified_upload(media_api, capsys):
    f = media_api
    f.uploaded.body["status"] = "failed"
    assert send_image(f) is None
    assert f.calls["get"] == []
    output = capsys.readouterr().out
    assert "upload_message_failed" in output and '"message_id": 22' in output


@pytest.mark.parametrize("field,value,reason", [
    ("message_id", 99, "upload_message_id_mismatch"),
    ("account_id", 11, "upload_account_id_mismatch"),
])
def test_unexpected_attachment_metadata_is_not_read_or_reuploaded(media_api, capsys, field, value, reason):
    f = media_api
    f.attachment[field] = value
    assert send_image(f) is None
    assert len(f.calls["post"]) == 1
    assert f.calls["get"] == []
    assert reason in capsys.readouterr().out


@pytest.mark.parametrize("failure,reason", [
    ("404", "asset_http_error"),
    ("html_bytes", "asset_bytes_mismatch"),
    ("different_image", "asset_bytes_mismatch"),
    ("large_header", "asset_too_large"),
    ("large_stream", "asset_too_large"),
])
def test_unreadable_or_changed_stored_image_fails_without_another_upload(media_api, capsys, failure, reason):
    f = media_api
    if failure == "404":
        f.asset.status_code = 404
    elif failure == "html_bytes":
        f.asset.data = b"<html>bad storage response</html>"
    elif failure == "different_image":
        f.asset.data = png_image(b"\x00\xff\x00")
    elif failure == "large_header":
        f.asset.headers["Content-Length"] = str(f.api.config.CHATWOOT_MAX_ATTACHMENT_BYTES + 1)
    else:
        f.api.config.CHATWOOT_MAX_ATTACHMENT_BYTES = len(f.image)
        f.asset.headers.pop("Content-Length")
        f.asset.data += b"extra"
    assert send_image(f) is None
    assert len(f.calls["post"]) == 1
    assert f.asset.closed
    output = capsys.readouterr().out
    assert reason in output
    assert "private-api-token" not in output
    assert "private-signature" not in output
    assert "https://" not in output


def test_storage_redirects_never_receive_api_authentication(media_api):
    f = media_api
    first = Response(302, headers={"Location": "/redirected/image.png"})
    second = Response(302, headers={"Location": "https://storage.example/test.png?signature=private-signature"})
    f.asset_responses[:] = [first, second, f.asset]

    assert send_image(f) is f.uploaded

    assert len(f.calls["get"]) == 3
    assert all(request["headers"] == {} and request["allow_redirects"] is False for _url, request in f.calls["get"])
    assert first.closed and second.closed and f.asset.closed
    assert len(f.calls["post"]) == 1


def test_self_hosted_storage_can_use_a_valid_custom_https_port(media_api):
    f = media_api
    f.attachment["data_url"] = "https://chatwoot.example:8443/image.png"
    assert send_image(f) is f.uploaded
    assert f.calls["get"][0][0] == f.attachment["data_url"]


def test_storage_redirect_limit_is_bounded_without_resubmitting(media_api, capsys):
    f = media_api
    redirects = [Response(302, headers={"Location": "/next.png"}) for _ in range(4)]
    f.asset_responses[:] = redirects
    assert send_image(f) is None
    assert len(f.calls["get"]) == 4
    assert len(f.calls["post"]) == 1
    assert all(response.closed for response in redirects)
    assert "asset_redirect_limit" in capsys.readouterr().out


def test_exactly_three_safe_storage_redirects_are_supported(media_api):
    f = media_api
    redirects = [Response(302, headers={"Location": f"/next-{index}.png"}) for index in range(3)]
    f.asset_responses[:] = [*redirects, f.asset]
    assert send_image(f) is f.uploaded
    assert len(f.calls["get"]) == 4
    assert all(response.closed for response in redirects)


@pytest.mark.parametrize("url", [
    "http://chatwoot.example/image.png", "https://user:secret@chatwoot.example/image.png",
    "https://chatwoot.example/image.png#fragment", "https://127.0.0.1/image.png",
    "https://169.254.169.254/image.png", "https://localhost/image.png", "javascript:alert(1)",
])
def test_unsafe_storage_urls_are_never_requested(media_api, url):
    f = media_api
    f.attachment["data_url"] = url
    assert send_image(f) is None
    assert f.calls["get"] == []
    assert len(f.calls["post"]) == 1


def test_storage_timeout_does_not_expose_signed_url_or_retry_the_upload(media_api, monkeypatch, capsys):
    f = media_api

    def fail(*args, **kwargs):
        raise requests.ReadTimeout("https://storage.example/test?signature=private-signature private-api-token")

    monkeypatch.setattr(f.api, "get_media_asset", fail)
    assert send_image(f) is None
    assert len(f.calls["post"]) == 1
    output = capsys.readouterr().out
    assert "provider_transport_error" in output
    assert "private-signature" not in output and "private-api-token" not in output


def test_upload_redirect_is_rejected_without_reading_or_reposting(media_api):
    f = media_api
    f.uploaded.status_code = 302
    assert send_image(f) is None
    assert len(f.calls["post"]) == 1
    assert f.calls["get"] == []


def test_real_asset_client_does_not_inherit_netrc_authentication(monkeypatch, tmp_path):
    import chatwoot_media

    netrc = tmp_path / "netrc"
    netrc.write_text("machine storage.example login test-user password test-password\n", encoding="utf-8")
    monkeypatch.setenv("NETRC", str(netrc))
    prepared_requests = []
    response = Response()

    def send(prepared, **kwargs):
        prepared_requests.append(prepared)
        return response

    monkeypatch.setattr(chatwoot_media._asset_session, "send", send)
    assert chatwoot_media.get_media_asset(
        "https://storage.example/image.png", headers={}, allow_redirects=False, stream=True, timeout=(3, 10),
    ) is response
    assert "Authorization" not in prepared_requests[0].headers
    assert "api_access_token" not in prepared_requests[0].headers


@pytest.mark.parametrize("ca_variable", ["REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"])
def test_asset_client_preserves_proxy_and_ca_settings_without_netrc_auth(monkeypatch, tmp_path, ca_variable):
    import chatwoot_media

    netrc = tmp_path / "netrc"
    netrc.write_text("machine storage.example login test-user password test-password\n", encoding="utf-8")
    monkeypatch.setenv("NETRC", str(netrc))
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:8081")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    ca_path = str(tmp_path / "deployment-ca.pem")
    monkeypatch.setenv(ca_variable, ca_path)
    captured = []
    response = Response()

    def send(prepared, **kwargs):
        captured.append((prepared, kwargs))
        return response

    monkeypatch.setattr(chatwoot_media._asset_session, "send", send)
    assert chatwoot_media.get_media_asset("https://storage.example/image.png", headers={}) is response
    prepared, transport = captured[0]
    assert "Authorization" not in prepared.headers
    assert "api_access_token" not in prepared.headers
    assert transport["proxies"]["https"] == "http://proxy.example:8080"
    assert transport["proxies"]["http"] == "http://proxy.example:8081"
    assert transport["verify"] == ca_path


def test_asset_client_honors_no_proxy_and_requests_ca_precedence(monkeypatch, tmp_path):
    import chatwoot_media

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("NO_PROXY", "storage.example")
    monkeypatch.setenv("no_proxy", "storage.example")
    preferred_ca = str(tmp_path / "requests-ca.pem")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", preferred_ca)
    monkeypatch.setenv("CURL_CA_BUNDLE", str(tmp_path / "curl-ca.pem"))
    captured = []

    def send(prepared, **kwargs):
        captured.append(kwargs)
        return Response()

    monkeypatch.setattr(chatwoot_media._asset_session, "send", send)
    chatwoot_media.get_media_asset("https://storage.example/image.png", headers={})
    assert captured[0]["proxies"] == {}
    assert captured[0]["verify"] == preferred_ca


@pytest.mark.parametrize("private", [False, True])
def test_audio_conversion_failure_never_attaches_original_ogg(media_api, monkeypatch, private):
    f = media_api
    notes = []
    monkeypatch.setattr(f.api, "_prepare_audio_for_chatwoot", lambda *args: (_ for _ in ()).throw(OSError("decoder unavailable")))
    monkeypatch.setattr(f.api, "send_media_to_chatwoot", lambda *args, **kwargs: pytest.fail("original audio must not be attached"))
    monkeypatch.setattr(f.api, "send_message_to_chatwoot", lambda *args, **kwargs: notes.append((args, kwargs)))

    assert f.api.send_audio_to_chatwoot(44, b"original-ogg", is_private=private, transcript="Texto de prueba") is None

    assert len(notes) == 1
    assert "Transcripción del audio: Texto de prueba" in notes[0][0][1]
    assert notes[0][1]["is_private"] is private
    assert f.calls["post"] == []
