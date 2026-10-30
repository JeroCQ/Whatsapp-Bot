"""Meta media downloads stay bounded before any Chatwoot upload or decoding."""

import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import patch

import pytest
import requests


class Response:
    def __init__(self, *, body=None, chunks=(), status=200, headers=None):
        self.body = body
        self.chunks = chunks
        self.status_code = status
        self.headers = headers or {}
        self.closed = False
        self.read = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("PRIVATE_RAW_RESPONSE", response=self)

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body

    def iter_content(self, size):
        assert size == 64 * 1024
        self.read = True
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    @property
    def content(self):
        pytest.fail("Meta download must not use eager response.content")

    def close(self):
        self.closed = True


@pytest.fixture
def download_api(monkeypatch):
    private_config = types.ModuleType("config")
    private_config.config = types.SimpleNamespace(
        WA_TOKEN="PRIVATE_API_TOKEN", CHATWOOT_MAX_ATTACHMENT_BYTES=1024,
    )
    spec = importlib.util.spec_from_file_location(
        "_bounded_meta_media_download", Path(__file__).resolve().parents[1] / "chatwoot_api.py",
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"config": private_config}):
        spec.loader.exec_module(module)
    metadata = Response(body={"url": "https://meta-assets.example/file?signature=PRIVATE_SIGNATURE", "mime_type": "image/webp"})
    asset = Response(chunks=(b"RIFF", b"original webp bytes"), headers={"Content-Length": "23", "Content-Type": "application/octet-stream"})
    calls = []
    responses = [metadata, asset]
    def get(url, **kwargs):
        calls.append((url, kwargs))
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(module, "get", get)
    return types.SimpleNamespace(api=module, metadata=metadata, asset=asset, responses=responses, calls=calls)


@pytest.mark.parametrize("business", ["tanaka", "memos", "velvet", "future_brand"])
def test_download_preserves_bytes_mime_and_own_credentials_while_streaming(download_api, business):
    f = download_api
    f.api.config.BUSINESS_ID = business
    f.api.config.WA_TOKEN = f"PRIVATE_{business}_TOKEN"
    expected = b"RIFForiginal webp bytes"
    assert f.api.download_meta_media("synthetic-media-id") == (expected, "image/webp")
    assert len(f.calls) == 2
    assert f.calls[0][1]["headers"] == {"Authorization": f"Bearer PRIVATE_{business}_TOKEN"}
    assert f.calls[1][1] == {"headers": {"Authorization": f"Bearer PRIVATE_{business}_TOKEN"},
                              "timeout": f.api.MEDIA_TIMEOUT, "stream": True}
    assert f.metadata.closed and f.asset.closed and f.asset.read


def test_download_uses_response_mime_when_metadata_omits_it(download_api):
    f = download_api
    f.metadata.body.pop("mime_type")
    assert f.api.download_meta_media("synthetic-media-id")[1] == "application/octet-stream"
    assert f.metadata.closed and f.asset.closed


@pytest.mark.parametrize("declared", [None, "1", "1025"])
def test_lower_operator_limit_is_enforced_by_header_and_counted_bytes(download_api, capsys, declared):
    f = download_api
    f.asset.headers = {"Content-Length": declared} if declared is not None else {}
    f.asset.chunks = (b"x" * 1024, b"y")
    assert f.api.download_meta_media("synthetic-media-id") == (None, None)
    assert "meta_media_too_large" in capsys.readouterr().out
    assert f.asset.read is (declared != "1025")
    assert f.metadata.closed and f.asset.closed


def test_provider_hundred_mib_cap_remains_even_if_local_setting_is_higher(download_api):
    f = download_api
    f.api.config.CHATWOOT_MAX_ATTACHMENT_BYTES = 200 * 1024 * 1024
    f.asset.headers["Content-Length"] = str(100 * 1024 * 1024 + 1)
    assert f.api.download_meta_media("synthetic-media-id") == (None, None)
    assert not f.asset.read and f.metadata.closed and f.asset.closed


@pytest.mark.parametrize("declared", ["-1", "invalid"])
def test_invalid_declared_length_does_not_read_media(download_api, declared):
    f = download_api
    f.asset.headers["Content-Length"] = declared
    assert f.api.download_meta_media("synthetic-media-id") == (None, None)
    assert not f.asset.read and f.metadata.closed and f.asset.closed


@pytest.mark.parametrize("failure", ["metadata_http", "metadata_json", "metadata_shape", "url_missing", "asset_http", "asset_transport", "stream_timeout", "stream_type", "empty"])
def test_failures_release_responses_and_never_log_provider_content_or_credentials(download_api, capsys, failure):
    f = download_api
    if failure == "metadata_http":
        f.metadata.status_code = 403
    elif failure == "metadata_json":
        f.metadata.body = ValueError("PRIVATE_METADATA_BODY")
    elif failure == "metadata_shape":
        f.metadata.body = ["PRIVATE_METADATA_BODY"]
    elif failure == "url_missing":
        f.metadata.body = {}
    elif failure == "asset_http":
        f.asset.status_code = 403
    elif failure == "asset_transport":
        f.responses[1] = requests.ReadTimeout("PRIVATE_SIGNATURE PRIVATE_API_TOKEN")
    elif failure == "stream_timeout":
        f.asset.chunks = (b"first bytes", requests.ReadTimeout("PRIVATE_SIGNATURE"))
    elif failure == "stream_type":
        f.asset.chunks = ("not byte data",)
    else:
        f.asset.chunks = ()
    assert f.api.download_meta_media("synthetic-media-id") == (None, None)
    assert f.metadata.closed
    assert f.asset.closed is (not failure.startswith("metadata_") and failure not in {"url_missing", "asset_transport"})
    assert len(f.calls) == (1 if failure.startswith("metadata_") or failure == "url_missing" else 2)
    output = capsys.readouterr().out
    assert "PRIVATE" not in output and "https://" not in output
