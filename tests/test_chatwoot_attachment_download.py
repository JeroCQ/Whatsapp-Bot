"""Chatwoot storage failures remain diagnosable without exposing signed URLs."""

import importlib
import json
import sys
import types

import pytest
import requests


class Response:
    def __init__(self, status=200, *, data=b"image-bytes", headers=None, body=None, stream_error=None):
        self.status_code = status
        self.headers = headers or {"Content-Type": "image/png"}
        self.data = data
        self.body = body or {"id": "media-test"}
        self.stream_error = stream_error
        self.closed = False

    def iter_content(self, chunk_size):
        if self.stream_error:
            raise self.stream_error
        yield self.data

    def json(self):
        return self.body

    def close(self):
        self.closed = True


@pytest.fixture
def downloader(monkeypatch):
    for name, value in {
        "BUSINESS_ID": "download_test", "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_KEY": "test", "WA_VERIFY_TOKEN": "test", "WA_TOKEN": "test",
        "WA_PHONE_NUMBER_ID": "test", "GEMINI_API_KEY": "test",
    }.items():
        monkeypatch.setenv(name, value)
    if "bot" not in sys.modules:
        bot = types.ModuleType("bot")
        bot.FILE_CATALOG = {}
        bot.process_message_logic = lambda *args: None
        bot.transcribe_audio_message = lambda *args: None
        monkeypatch.setitem(sys.modules, "bot", bot)
    main = importlib.import_module("main")
    for name, value in {
        "CHATWOOT_BASE_URL": "https://chatwoot.example",
        "CHATWOOT_API_TOKEN": "private-chatwoot-token", "WA_TOKEN": "private-meta-token",
        "WA_PHONE_NUMBER_ID": "test-number", "CHATWOOT_MAX_ATTACHMENT_BYTES": 1024,
    }.items():
        monkeypatch.setattr(main.config, name, value)
    origin_responses = [Response()]
    storage_responses = []
    uploaded = Response()
    calls = {"origin": [], "storage": [], "post": []}

    def origin(url, **kwargs):
        assert url.startswith("https://chatwoot.example/")
        calls["origin"].append((url, kwargs))
        return origin_responses.pop(0)

    def storage(url, **kwargs):
        calls["storage"].append((url, kwargs))
        return storage_responses.pop(0)

    def post(url, **kwargs):
        calls["post"].append((url, kwargs))
        return uploaded

    monkeypatch.setattr(main, "get", origin)
    monkeypatch.setattr(main, "get_media_asset", storage)
    monkeypatch.setattr(main, "post", post)
    return types.SimpleNamespace(main=main, origin=origin_responses, storage=storage_responses,
        uploaded=uploaded, calls=calls)


def diagnostics(capsys):
    output = capsys.readouterr().out
    line = next(line for line in output.splitlines() if line.startswith("[CHATWOOT ATTACHMENT] "))
    assert "private-signature" not in output
    assert "private-chatwoot-token" not in output
    assert "private-meta-token" not in output
    assert "private-error-body" not in output
    assert "https://" not in line
    return json.loads(line.removeprefix("[CHATWOOT ATTACHMENT] "))


def test_relative_attachment_keeps_origin_auth_and_closes_download_and_meta_response(downloader):
    f = downloader
    origin = f.origin[0]
    assert f.main.upload_chatwoot_attachment_to_meta("/rails/file.png") == "media-test"
    assert f.calls["origin"][0][1]["headers"] == {"api_access_token": "private-chatwoot-token"}
    assert f.calls["storage"] == []
    assert len(f.calls["post"]) == 1
    assert f.calls["post"][0][1]["allow_redirects"] is False
    assert origin.closed and f.uploaded.closed


@pytest.mark.parametrize("code", ["AccessDenied", "SignatureDoesNotMatch", "InvalidAccessKeyId", "NoSuchKey"])
def test_storage_403_reports_only_allowlisted_code_and_never_uploads_to_meta(downloader, capsys, code):
    f = downloader
    redirect = Response(302, headers={"Location": "https://storage.example/file?signature=private-signature"})
    denied = Response(403, data=f"<Error><Code>{code}</Code><Message>private-error-body</Message></Error>".encode())
    f.origin[:] = [redirect]
    f.storage[:] = [denied]

    assert f.main.upload_chatwoot_attachment_to_meta("/rails/file.png") is None

    event = diagnostics(capsys)
    assert event["phase"] == "storage_download"
    assert event["host"] == "storage.example"
    assert event["http_status"] == 403
    assert event["storage_error_code"] == code
    assert f.calls["storage"][0][1]["headers"] == {}
    assert len(f.calls["origin"]) == len(f.calls["storage"]) == 1
    assert f.calls["post"] == []
    assert redirect.closed and denied.closed


def test_unrecognized_storage_error_never_logs_its_body(downloader, capsys):
    f = downloader
    denied = Response(403, data=b"<Error><Code>private-error-body</Code></Error>")
    f.origin[:] = [Response(302, headers={"Location": "https://storage.example/file"})]
    f.storage[:] = [denied]
    assert f.main.upload_chatwoot_attachment_to_meta("/file") is None
    assert diagnostics(capsys)["storage_error_code"] == ""
    assert denied.closed


def test_storage_read_failure_closes_response_without_retry_or_raw_exception(downloader, capsys):
    f = downloader
    broken = Response(stream_error=requests.ReadTimeout("https://storage.example/file?signature=private-signature"))
    f.origin[:] = [Response(302, headers={"Location": "https://storage.example/file"})]
    f.storage[:] = [broken]
    assert f.main.upload_chatwoot_attachment_to_meta("/file") is None
    assert diagnostics(capsys)["reason"] == "attachment_transport_error"
    assert len(f.calls["storage"]) == 1
    assert f.calls["post"] == []
    assert broken.closed


@pytest.mark.parametrize("target", [
    "http://storage.example/file", "https://user:password@storage.example/file",
    "https://127.0.0.1/file", "https://169.254.169.254/file",
])
def test_unsafe_redirect_does_not_receive_credentials_or_trigger_a_request(downloader, target):
    f = downloader
    redirect = Response(302, headers={"Location": target})
    f.origin[:] = [redirect]
    assert f.main.upload_chatwoot_attachment_to_meta("/file") is None
    assert redirect.closed
    assert f.calls["storage"] == f.calls["post"] == []


def test_three_storage_redirects_succeed_with_no_api_credentials(downloader):
    f = downloader
    redirects = [Response(302, headers={"Location": "https://storage.example/file"}) for _ in range(3)]
    asset = Response()
    f.origin[:] = redirects[:1]
    f.storage[:] = [*redirects[1:], asset]
    assert f.main.upload_chatwoot_attachment_to_meta("/file") == "media-test"
    assert all(request["headers"] == {} for _url, request in f.calls["storage"])
    assert all(response.closed for response in [*redirects, asset, f.uploaded])
    assert len(f.calls["post"]) == 1


def test_fourth_storage_redirect_is_not_followed(downloader, capsys):
    f = downloader
    redirects = [Response(302, headers={"Location": "https://storage.example/file"}) for _ in range(4)]
    f.origin[:] = redirects[:1]
    f.storage[:] = redirects[1:]
    assert f.main.upload_chatwoot_attachment_to_meta("/file") is None
    assert diagnostics(capsys)["reason"] == "attachment_redirect_limit"
    assert len(f.calls["storage"]) == 3
    assert all(response.closed for response in redirects)
    assert f.calls["post"] == []


@pytest.mark.parametrize("declared", [False, True])
def test_oversized_download_is_closed_before_any_meta_upload(downloader, capsys, declared):
    f = downloader
    f.main.config.CHATWOOT_MAX_ATTACHMENT_BYTES = 2
    large = Response(data=b"abc", headers={"Content-Length": "3"} if declared else {})
    f.origin[:] = [large]
    assert f.main.upload_chatwoot_attachment_to_meta("/file") is None
    assert diagnostics(capsys)["reason"] == "attachment_too_large"
    assert large.closed
    assert f.calls["post"] == []


def test_meta_rejection_is_distinct_from_storage_failure_and_not_retried(downloader, capsys):
    f = downloader
    f.uploaded.status_code = 403
    assert f.main.upload_chatwoot_attachment_to_meta("/file") is None
    event = diagnostics(capsys)
    assert event["phase"] == "meta_upload" and event["host"] == "graph.facebook.com"
    assert event["http_status"] == 403 and event["storage_error_code"] == ""
    assert len(f.calls["post"]) == 1
    assert f.uploaded.closed
