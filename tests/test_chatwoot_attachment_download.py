"""Chatwoot storage failures remain diagnosable without exposing signed URLs."""

import importlib
import hashlib
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
        "CHATWOOT_MAX_DOCUMENT_BYTES": 100 * 1024 * 1024,
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


class PdfResponse(Response):
    """Generate a large synthetic PDF stream without allocating the complete file."""

    def __init__(self, size, *, declared=None, mime="application/pdf"):
        headers = {"Content-Type": mime}
        if declared is not None:
            headers["Content-Length"] = str(declared)
        super().__init__(headers=headers)
        self.size = size

    def iter_content(self, chunk_size):
        prefix = b"%PDF-1.7\n"
        yield prefix
        remaining = self.size - len(prefix)
        chunk = b"0" * chunk_size
        while remaining:
            piece = chunk[:min(remaining, chunk_size)]
            yield piece
            remaining -= len(piece)


@pytest.fixture
def pdf_files(monkeypatch, downloader):
    files = []
    create_file = downloader.main.tempfile.TemporaryFile

    def tracked_file(*args, **kwargs):
        created = create_file(*args, **kwargs)
        files.append(created)
        return created

    monkeypatch.setattr(downloader.main.tempfile, "TemporaryFile", tracked_file)
    return files


@pytest.mark.parametrize("business", ["tanaka", "memos", "velvet", "future_brand"])
def test_pdf_above_old_limit_streams_once_to_own_meta_number(monkeypatch, downloader, pdf_files, capsys, business):
    f = downloader
    size = 26 * 1024 * 1024
    source = PdfResponse(size, declared=size)
    f.origin[:] = [source]
    monkeypatch.setattr(f.main.config, "BUSINESS_ID", business)
    monkeypatch.setattr(f.main.config, "WA_PHONE_NUMBER_ID", business + "-number")

    def post(url, **kwargs):
        f.calls["post"].append((url, kwargs))
        assert url.endswith(f"/{business}-number/media")
        assert "files" not in kwargs
        encoder = kwargs["data"]
        assert isinstance(encoder, f.main.MultipartEncoder)
        file_name, handle, mime = encoder.fields["file"]
        assert (file_name, mime) == ("portfolio.pdf", "application/pdf")
        assert not handle.closed
        expected_hash = hashlib.sha256()
        for chunk in source.iter_content(64 * 1024):
            expected_hash.update(chunk)
        actual_hash = hashlib.sha256()
        while chunk := handle.read(64 * 1024):
            actual_hash.update(chunk)
        assert actual_hash.digest() == expected_hash.digest()
        handle.seek(0)
        prepared = requests.Request("POST", url, headers=kwargs["headers"], data=encoder).prepare()
        assert prepared.body is encoder  # requests must not buffer the PDF.
        assert prepared.headers["Content-Length"] == str(encoder.len)
        # Exercise the actual encoder in bounded reads, as requests does; a
        # files= payload or an eager bytes body would fail this assertion.
        assert encoder.fields["messaging_product"] == "whatsapp"
        length = 0
        prefix = b""
        while chunk := encoder.read(64 * 1024):
            if not prefix:
                prefix = chunk
            assert len(chunk) <= 64 * 1024
            length += len(chunk)
        assert b"%PDF-1.7" in prefix
        assert size < length == encoder.len == int(kwargs["headers"]["Content-Length"])
        assert kwargs["headers"]["Content-Type"] == encoder.content_type
        return f.uploaded

    monkeypatch.setattr(f.main, "post", post)
    assert f.main.upload_chatwoot_attachment_to_meta("/portfolio.pdf", filename="portfolio.pdf") == "media-test"
    event = diagnostics(capsys)
    assert event["maximum_bytes"] == 100 * 1024 * 1024
    assert event["declared_bytes"] == event["downloaded_bytes"] == size
    assert event["media_kind"] == "document"
    assert len(f.calls["post"]) == len(pdf_files) == 1
    assert source.closed and f.uploaded.closed and pdf_files[0].closed


@pytest.mark.parametrize("declared", [None, 1, 100 * 1024 * 1024 + 1])
def test_pdf_above_provider_limit_never_reaches_meta(downloader, pdf_files, capsys, declared):
    f = downloader
    # The provider cap still wins over an accidentally excessive local setting.
    f.main.config.CHATWOOT_MAX_DOCUMENT_BYTES = 200 * 1024 * 1024
    source = PdfResponse(100 * 1024 * 1024 + 1, declared=declared)
    f.origin[:] = [source]
    assert f.main.upload_chatwoot_attachment_to_meta("/portfolio.pdf", filename="portfolio.pdf") is None
    event = diagnostics(capsys)
    assert event["reason"] == "attachment_too_large"
    assert event["maximum_bytes"] == 100 * 1024 * 1024
    assert source.closed and all(handle.closed for handle in pdf_files)
    assert f.calls["post"] == []
    if declared and declared > event["maximum_bytes"]:
        assert pdf_files == []


def test_configured_lower_document_limit_is_enforced(downloader, pdf_files, capsys):
    f = downloader
    f.main.config.CHATWOOT_MAX_DOCUMENT_BYTES = 16
    source = PdfResponse(17)
    f.origin[:] = [source]
    assert f.main.upload_chatwoot_attachment_to_meta("/portfolio.pdf", filename="portfolio.pdf") is None
    assert diagnostics(capsys)["maximum_bytes"] == 16
    assert source.closed and pdf_files[0].closed and f.calls["post"] == []


@pytest.mark.parametrize("mime, fallback, filename", [
    ("application/pdf", "application/octet-stream", "portfolio.pdf"),
    ("application/octet-stream", "application/pdf", "portfolio"),
    ("application/octet-stream", "application/octet-stream", "portfolio.PDF"),
])
def test_pdf_type_can_use_metadata_or_generic_mime_extension(downloader, pdf_files, mime, fallback, filename):
    f = downloader
    f.origin[:] = [PdfResponse(2048, mime=mime)]
    assert f.main.upload_chatwoot_attachment_to_meta("/portfolio", fallback, filename) == "media-test"
    assert f.calls["post"][0][1]["data"].fields["file"][2] == "application/pdf"
    assert pdf_files[0].closed


@pytest.mark.parametrize("mime, filename", [("image/jpeg", "misleading.pdf"), ("application/msword", "portfolio.doc")])
def test_non_pdf_retains_original_attachment_limit(downloader, pdf_files, mime, filename):
    f = downloader
    f.origin[:] = [PdfResponse(2048, declared=2048, mime=mime)]
    assert f.main.upload_chatwoot_attachment_to_meta("/file", filename=filename) is None
    assert f.calls["post"] == pdf_files == []


def test_pdf_extension_cannot_disguise_non_pdf_content(downloader, pdf_files, capsys):
    f = downloader
    source = Response(data=b"not a PDF", headers={"Content-Type": "application/octet-stream"})
    f.origin[:] = [source]
    assert f.main.upload_chatwoot_attachment_to_meta("/portfolio.pdf", filename="portfolio.pdf") is None
    assert diagnostics(capsys)["reason"] == "attachment_pdf_signature_mismatch"
    assert source.closed and pdf_files[0].closed and f.calls["post"] == []


@pytest.mark.parametrize("failure", ["storage", "meta_transport", "meta_rejected", "expired_window"])
def test_pdf_temporary_file_is_closed_on_failure_without_post_retry(monkeypatch, downloader, pdf_files, failure):
    f = downloader
    source = PdfResponse(2048)
    f.origin[:] = [source]
    if failure == "storage":
        def broken_stream(_chunk_size):
            yield b"%PDF-1.7\n"
            raise requests.ReadTimeout("private-signature")
        source.iter_content = broken_stream
    elif failure == "meta_transport":
        def failed_post(*args, **kwargs):
            f.calls["post"].append((args, kwargs))
            raise requests.ReadTimeout("provider outcome unknown")
        monkeypatch.setattr(f.main, "post", failed_post)
    elif failure == "meta_rejected":
        f.uploaded.status_code = 400
    else:
        def expired():
            raise f.main.ProviderError("meta", "window_validation", 400, message="original_customer_window_expired")
        monkeypatch.setattr(f.main, "_check_customer_window", expired)
    assert f.main.upload_chatwoot_attachment_to_meta("/portfolio.pdf", filename="portfolio.pdf") is None
    assert source.closed and pdf_files[0].closed
    assert len(f.calls["post"]) == (1 if failure.startswith("meta_") else 0)


def test_pdf_download_deadline_releases_file_before_contacting_meta(monkeypatch, downloader, pdf_files, capsys):
    f = downloader
    source = PdfResponse(2048)
    f.origin[:] = [source]
    moments = iter([0.0, 61.0])
    monkeypatch.setattr(f.main.time, "monotonic", lambda: next(moments))
    assert f.main.upload_chatwoot_attachment_to_meta("/portfolio.pdf", filename="portfolio.pdf") is None
    assert diagnostics(capsys)["reason"] == "attachment_download_timeout"
    assert source.closed and pdf_files[0].closed and f.calls["post"] == []


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


def test_meta_error_records_numeric_code_without_provider_body(downloader, capsys):
    f = downloader
    f.uploaded.status_code = 400
    f.uploaded.body = {"error": {"code": 100, "error_subcode": 2018001,
                                  "message": "private-error-body private-meta-token"}}
    assert f.main.upload_chatwoot_attachment_to_meta("/image.png") is None
    event = diagnostics(capsys)
    assert event["provider_error_code"] == "100"
    assert event["provider_error_subcode"] == "2018001"
    assert len(f.calls["post"]) == 1


def test_oversized_image_is_prepared_before_the_only_meta_upload(downloader, monkeypatch):
    f = downloader
    original = b"large-photo"
    f.origin[:] = [Response(data=original, headers={"Content-Type": "image/png"})]
    prepared = []

    def prepare(data, mime):
        prepared.append((data, mime))
        return b"small-jpeg", "image/jpeg", ".jpg"

    monkeypatch.setattr(f.main, "prepare_image_for_whatsapp", prepare)
    assert f.main.upload_chatwoot_attachment_to_meta("/photo.png", filename="photo.png") == "media-test"
    assert prepared == [(original, "image/png")]
    assert f.calls["post"][0][1]["files"]["file"] == ("photo.jpg", b"small-jpeg", "image/jpeg")
    assert len(f.calls["post"]) == 1


def test_invalid_image_preparation_cannot_reach_meta(downloader, monkeypatch, capsys):
    f = downloader

    def prepare(*_args):
        raise f.main.MediaValidationError("attachment_image_prepare_failed")

    monkeypatch.setattr(f.main, "prepare_image_for_whatsapp", prepare)
    assert f.main.upload_chatwoot_attachment_to_meta("/photo.png") is None
    assert f.calls["post"] == []
    assert diagnostics(capsys)["reason"] == "attachment_image_prepare_failed"


def test_generic_http_mime_uses_attachment_metadata(downloader):
    f = downloader
    f.origin[:] = [Response(headers={"Content-Type": "application/octet-stream"})]
    assert f.main.upload_chatwoot_attachment_to_meta("/video", "video/mp4", "video.mp4") == "media-test"
    assert f.calls["post"][0][1]["files"]["file"][2] == "video/mp4"


@pytest.fixture
def pending_upload(monkeypatch, downloader):
    f = downloader
    clock = types.SimpleNamespace(elapsed=0.0, sleeps=[])

    def sleep(seconds):
        clock.sleeps.append(seconds)
        clock.elapsed += seconds

    monkeypatch.setattr(f.main, "time", types.SimpleNamespace(
        monotonic=lambda: clock.elapsed, sleep=sleep,
    ))
    f.clock = clock
    f.created_at = f.main.datetime.now(f.main.timezone.utc).isoformat()
    f.responses = []

    def origin(url, **kwargs):
        assert url == "https://chatwoot.example/video"
        f.calls["origin"].append((url, kwargs))
        response = Response(302, headers={
            "Location": f"https://storage.example/video?signature=private-signature-{len(f.calls['origin'])}",
        })
        f.responses.append(response)
        return response

    monkeypatch.setattr(f.main, "get", origin)
    return f


def test_recent_attachment_waits_for_storage_upload_and_refreshes_signed_url(monkeypatch, pending_upload, capsys):
    f = pending_upload

    def storage(url, **kwargs):
        f.calls["storage"].append((url, kwargs))
        response = (Response(404, data=b"<Error><Code>NoSuchKey</Code></Error>")
                    if f.clock.elapsed < 10 else Response(headers={"Content-Type": "video/mp4"}))
        f.responses.append(response)
        return response

    monkeypatch.setattr(f.main, "get_media_asset", storage)
    assert f.main.upload_chatwoot_attachment_to_meta(
        "/video", "video/mp4", "video.mp4", attachment_created_at=f.created_at,
    ) == "media-test"

    assert f.clock.sleeps == [1, 2, 4, 8]
    assert len(f.calls["origin"]) == len(f.calls["storage"]) == 5
    assert len({url for url, _kwargs in f.calls["storage"]}) == 5
    assert all(kwargs["headers"] == {} for _url, kwargs in f.calls["storage"])
    assert all(kwargs["headers"] == {"api_access_token": "private-chatwoot-token"}
               for _url, kwargs in f.calls["origin"])
    assert len(f.calls["post"]) == 1
    assert all(response.closed for response in [*f.responses, f.uploaded])
    event = diagnostics(capsys)
    assert event["pending_upload_retries"] == 4
    assert event["storage_error_code"] == ""


def test_recent_attachment_stops_after_four_pending_upload_retries(monkeypatch, pending_upload, capsys):
    f = pending_upload

    def storage(url, **kwargs):
        f.calls["storage"].append((url, kwargs))
        response = Response(404, data=b"<Error><Code>NoSuchKey</Code></Error>")
        f.responses.append(response)
        return response

    monkeypatch.setattr(f.main, "get_media_asset", storage)
    assert f.main.upload_chatwoot_attachment_to_meta("/video", attachment_created_at=f.created_at) is None
    assert f.clock.sleeps == [1, 2, 4, 8]
    assert len(f.calls["origin"]) == len(f.calls["storage"]) == 5
    assert f.calls["post"] == []
    assert all(response.closed for response in f.responses)
    assert diagnostics(capsys)["storage_error_code"] == "NoSuchKey"


@pytest.mark.parametrize("created_at", [None, "invalid", "2026-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00"])
def test_missing_old_or_future_timestamp_does_not_wait_for_missing_object(monkeypatch, pending_upload, created_at):
    f = pending_upload
    response = Response(404, data=b"<Error><Code>NoSuchKey</Code></Error>")
    monkeypatch.setattr(f.main, "get_media_asset", lambda *args, **kwargs: response)
    assert f.main.upload_chatwoot_attachment_to_meta("/video", attachment_created_at=created_at) is None
    assert len(f.calls["origin"]) == 1
    assert f.clock.sleeps == []
    assert f.calls["post"] == []
    assert response.closed


@pytest.mark.parametrize(("status", "code"), [
    (403, "NoSuchKey"), (403, "SignatureDoesNotMatch"), (403, "AccessDenied"),
    (404, "NoSuchBucket"), (404, ""), (500, "NoSuchKey"),
])
def test_recent_timestamp_does_not_retry_other_storage_errors(monkeypatch, pending_upload, status, code):
    f = pending_upload
    response = Response(status, data=f"<Error><Code>{code}</Code></Error>".encode())
    monkeypatch.setattr(f.main, "get_media_asset", lambda *args, **kwargs: response)
    assert f.main.upload_chatwoot_attachment_to_meta("/video", attachment_created_at=f.created_at) is None
    assert len(f.calls["origin"]) == 1
    assert f.clock.sleeps == []
    assert f.calls["post"] == []
    assert response.closed


def test_pending_upload_wait_counts_network_time_against_budget(monkeypatch, pending_upload, capsys):
    f = pending_upload
    response = Response(404, data=b"<Error><Code>NoSuchKey</Code></Error>")

    def storage(*args, **kwargs):
        f.clock.elapsed = 19.5
        return response

    monkeypatch.setattr(f.main, "get_media_asset", storage)
    assert f.main.upload_chatwoot_attachment_to_meta("/video", attachment_created_at=f.created_at) is None
    assert f.clock.sleeps == []
    assert f.calls["post"] == []
    assert response.closed
    assert diagnostics(capsys)["reason"] == "attachment_pending_upload_timeout"


def test_pending_upload_retry_checks_window_after_wait(monkeypatch, pending_upload):
    f = pending_upload
    response = Response(404, data=b"<Error><Code>NoSuchKey</Code></Error>")
    monkeypatch.setattr(f.main, "get_media_asset", lambda *args, **kwargs: response)

    def check_window():
        if f.clock.elapsed:
            raise f.main.ProviderError("meta", "window_validation", 400, message="original_customer_window_expired")

    monkeypatch.setattr(f.main, "_check_customer_window", check_window)
    assert f.main.upload_chatwoot_attachment_to_meta("/video", attachment_created_at=f.created_at) is None
    assert f.clock.sleeps == [1]
    assert len(f.calls["origin"]) == 1
    assert f.calls["post"] == []
    assert response.closed


def test_pending_upload_retry_cannot_overrun_budget_during_download(monkeypatch, pending_upload, capsys):
    f = pending_upload
    missing = Response(404, data=b"<Error><Code>NoSuchKey</Code></Error>")
    delayed = Response()

    def chunks(chunk_size):
        f.clock.elapsed = 21
        yield b"late-video-bytes"

    delayed.iter_content = chunks
    responses = [missing, delayed]
    monkeypatch.setattr(f.main, "get_media_asset", lambda *args, **kwargs: responses.pop(0))
    assert f.main.upload_chatwoot_attachment_to_meta("/video", attachment_created_at=f.created_at) is None
    assert f.clock.sleeps == [1]
    assert f.calls["post"] == []
    assert missing.closed and delayed.closed
    assert diagnostics(capsys)["reason"] == "attachment_pending_upload_timeout"


def test_meta_transport_error_after_pending_upload_is_not_retried(monkeypatch, pending_upload, capsys):
    f = pending_upload
    responses = [Response(404, data=b"<Error><Code>NoSuchKey</Code></Error>"), Response()]
    monkeypatch.setattr(f.main, "get_media_asset", lambda *args, **kwargs: responses.pop(0))

    def post(*args, **kwargs):
        f.calls["post"].append((args, kwargs))
        raise requests.ReadTimeout("provider result unknown")

    monkeypatch.setattr(f.main, "post", post)
    assert f.main.upload_chatwoot_attachment_to_meta("/video", attachment_created_at=f.created_at) is None
    assert f.clock.sleeps == [1]
    assert len(f.calls["post"]) == 1
    assert diagnostics(capsys)["reason"] == "attachment_transport_error"
