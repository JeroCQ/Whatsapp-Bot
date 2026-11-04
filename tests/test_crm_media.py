"""Bot media lifecycle with synthetic storage/Meta; no network or external sends."""

from __future__ import annotations

import copy
import hashlib
from contextlib import nullcontext
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crm_media import MediaService, media_enabled
from crm_media_policy import LIMITS
from human_control_store import ControlError
from provider_errors import ProviderError
from test_crm_media_policy import samples as _samples_fixture

samples = _samples_fixture

PHONE = "573000000001"
ACTOR = "synthetic.operator"
SECRET = "synthetic-control-" + "s" * 40


class Query:
    def __init__(self, database):
        self.database, self.filters = database, {}
        self.operation, self.values = "select", None

    def select(self, _fields):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, _count):
        return self

    def insert(self, values):
        self.operation, self.values = "insert", copy.deepcopy(values)
        return self

    def update(self, values):
        self.operation, self.values = "update", copy.deepcopy(values)
        return self

    def execute(self):
        if self.operation == "insert":
            key = (self.values["business_id"], self.values["id"])
            if key in self.database.rows:
                raise RuntimeError("synthetic duplicate key")
            self.database.rows[key] = self.values
            return SimpleNamespace(data=[copy.deepcopy(self.values)])
        matched = [
            value
            for value in self.database.rows.values()
            if all(value.get(key) == expected for key, expected in self.filters.items())
        ]
        if self.operation == "update":
            for value in matched:
                value.update(self.values)
        return SimpleNamespace(data=copy.deepcopy(matched))


class Database:
    def __init__(self):
        self.rows = {}

    def table(self, name):
        assert name == "crm_media_assets"
        return Query(self)


class Response:
    def __init__(self, status=200, *, data=None, body=b"", headers=None):
        self.status_code, self.data, self.body = status, data, body
        self.headers, self.closed = headers or {}, False

    def json(self):
        if self.data is None:
            raise ValueError("synthetic non-JSON")
        return self.data

    def iter_content(self, size):
        for offset in range(0, len(self.body), size):
            yield self.body[offset : offset + size]

    def close(self):
        self.closed = True


class StorageAndMeta:
    """Only fake exact origins are recognized; unexpected I/O fails the test."""

    def __init__(self):
        self.objects, self.inbound, self.calls = {}, {}, []
        self.public, self.expire_once = False, False
        self.media_mode = "accepted"

    @staticmethod
    def key(url):
        path = urlsplit(url).path
        return path.split("/storage/v1/object/", 1)[1].removeprefix("authenticated/")

    def get(self, url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        self.calls.append(("GET", url, kwargs.get("headers", {})))
        if url.startswith("https://synthetic.supabase.co/storage/v1/bucket/"):
            return Response(data={"public": self.public})
        if url.startswith("https://synthetic.supabase.co/storage/v1/object/"):
            value = self.objects.get(self.key(url))
            if value is None:
                return Response(404)
            body, mime = value
            requested = kwargs.get("headers", {}).get("Range")
            if requested:
                first, last = [int(part) for part in requested[6:].split("-")]
                return Response(
                    206,
                    body=body[first : last + 1],
                    headers={
                        "Content-Range": f"bytes {first}-{last}/{len(body)}",
                        "Content-Type": mime,
                    },
                )
            return Response(
                body=body,
                headers={"Content-Length": str(len(body)), "Content-Type": mime},
            )
        if url.startswith("https://graph.facebook.com/v20.0/"):
            identity = url.rsplit("/", 1)[1]
            body, mime = self.inbound[identity]
            suffix = "expired" if self.expire_once else identity
            return Response(
                data={
                    "url": "https://lookaside.fbsbx.com/" + suffix,
                    "mime_type": mime,
                    "file_size": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                }
            )
        if url == "https://lookaside.fbsbx.com/expired":
            self.expire_once = False
            return Response(403)
        if url.startswith("https://lookaside.fbsbx.com/"):
            body, mime = self.inbound[url.rsplit("/", 1)[1]]
            return Response(
                body=body,
                headers={"Content-Length": str(len(body)), "Content-Type": mime},
            )
        pytest.fail("Unexpected external request: " + url)

    def post(self, url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        self.calls.append(("POST", url, kwargs.get("headers", {})))
        if url.startswith("https://synthetic.supabase.co/storage/v1/object/"):
            body = kwargs["data"].read()
            key = self.key(url)
            if key in self.objects:
                return Response(409)
            self.objects[key] = body, kwargs["headers"]["Content-Type"]
            return Response(201)
        if url == "https://graph.facebook.com/v20.0/123/media":
            if self.media_mode == "timeout":
                raise requests.ReadTimeout("synthetic media upload timeout")
            if self.media_mode == "rejected":
                return Response(
                    400,
                    data={
                        "error": {"code": 131053, "message": "synthetic unsupported"}
                    },
                )
            return Response(data={"id": "synthetic_uploaded_media"})
        pytest.fail("Unexpected external request: " + url)

    def head(self, url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        value = self.objects.get(self.key(url))
        return Response(
            200 if value else 404,
            headers={"Content-Length": str(len(value[0])) if value else "0"},
        )


class Store:
    def __init__(self, business):
        self.business, self.attempts = business, {}
        self.version, self.owner, self.phase = 3, ACTOR, "human"

    def get_control(self, phone):
        return {
            "business_id": self.business,
            "phone_number": phone,
            "provider": "crm",
            "phase": self.phase,
            "is_paused": self.phase == "human",
            "confirmed": self.phase == "human",
            "owner": self.owner,
            "version": self.version,
            "can_send": True,
            "inbound_message_ids": ["synthetic_inbound"],
            "window_expires_at": "2099-01-01T00:00:00Z",
        }

    def get_command(self, phone, command_id):
        if command_id not in self.attempts:
            raise ControlError("command_not_found", 404)
        return {**self.attempts[command_id], "control": self.get_control(phone)}

    def begin_send(
        self,
        phone,
        command_id,
        _kind,
        actor,
        version,
        digest,
        text,
        inbound,
        attachments=None,
    ):
        if command_id in self.attempts:
            prior = self.attempts[command_id]
            if prior["digest"] != digest or prior["phone"] != phone:
                raise ControlError("idempotency_conflict", 409)
            return {**prior, "control": self.get_control(phone)}
        if version != self.version or self.owner != actor or self.phase != "human":
            raise ControlError("control_version_conflict", 409)
        self.version += 1
        self.attempts[command_id] = {
            "command_id": command_id,
            "status": "sending",
            "digest": digest,
            "phone": phone,
            "attachments": attachments or [],
            "text": text,
        }
        return {
            "command_id": command_id,
            "status": "reserved",
            "control": self.get_control(phone),
        }

    def finish_send(self, command_id, status, provider_id, detail, *, phone):
        self.attempts[command_id].update(
            status=status, provider_message_id=provider_id, error=detail
        )
        return {**self.attempts[command_id], "control": self.get_control(phone)}


@pytest.fixture
def media(monkeypatch):
    for key, value in {
        "BUSINESS_ID": "briosos",
        "SUPABASE_URL": "https://synthetic.supabase.co",
        "SUPABASE_KEY": "synthetic-storage-key",
        "WA_VERIFY_TOKEN": "synthetic",
        "WA_TOKEN": "synthetic-meta-key",
        "WA_PHONE_NUMBER_ID": "123",
        "GEMINI_API_KEY": "synthetic",
    }.items():
        monkeypatch.setenv(key, value)
    from human_control_api import create_router
    from observability import update_trace

    config = SimpleNamespace(
        BUSINESS_ID="briosos",
        HUMAN_SUPPORT_PROVIDER="crm",
        CRM_CONTROL_ENABLED=True,
        CRM_BOT_CONTROL_SECRET=SECRET,
        CRM_MEDIA_ENABLED=True,
        CRM_MEDIA_ALLOWED_BUSINESSES="",
        CRM_MEDIA_BUCKET="crm-private-media",
        SUPABASE_URL="https://synthetic.supabase.co",
        SUPABASE_KEY="synthetic-storage-secret",
        WA_TOKEN="synthetic-meta-secret",
        WA_PHONE_NUMBER_ID="123",
    )
    db, http, sent, store = Database(), StorageAndMeta(), [], Store(config.BUSINESS_ID)
    service = MediaService(config, db, transport=http)

    def sender(*args):
        sent.append(args)
        update_trace(provider_message_id="wamid.SYNTHETIC.1")
        return True

    app = FastAPI()
    app.include_router(
        create_router(
            config,
            lambda: store,
            lambda *_: True,
            lambda *_: [],
            lambda *_: None,
            lambda *_: nullcontext(),
            media_factory=lambda: service,
            send_media=sender,
        )
    )
    return SimpleNamespace(
        service=service,
        store=store,
        http=http,
        db=db,
        sent=sent,
        config=config,
        client=TestClient(app),
        base=f"/internal/crm/v1/businesses/{config.BUSINESS_ID}/conversations/{PHONE}",
        headers={"X-CRM-Bot-Secret": SECRET, "X-CRM-Actor": ACTOR},
    )


def upload(media, path, mime="application/pdf", *, identity=None, headers=None):
    data = path.read_bytes()
    return media.client.post(
        media.base + "/attachments",
        params={
            "upload_id": identity or str(uuid4()),
            "filename": path.name,
            "expected_version": media.store.version,
        },
        content=data,
        headers={
            **media.headers,
            "Content-Type": mime,
            "X-Upload-Size": str(len(data)),
            **(headers or {}),
        },
    )


def test_media_default_disabled_and_no_brand_is_hardcoded(media):
    for business in ("briosos", "tanaka", "memos", "velvet", "future_business"):
        media.config.BUSINESS_ID = business
        assert media_enabled(media.config)
    media.config.CRM_MEDIA_ALLOWED_BUSINESSES = "tanaka,velvet"
    assert not media_enabled(media.config)
    media.config.CRM_MEDIA_ENABLED = False
    assert not media_enabled(media.config)


def test_readiness_requires_a_private_bucket(media):
    assert media.service.readiness()["storage_ready"]
    media.http.public = True
    assert not media.service.readiness()["storage_ready"]
    media.config.CRM_MEDIA_ENABLED = False
    media.http.calls.clear()
    assert not media.service.readiness()["media_enabled"]
    assert media.http.calls == []


@pytest.mark.parametrize(
    "name,mime",
    [
        ("image.png", "image/png"),
        ("sound.ogg", "audio/ogg"),
        ("document.pdf", "application/pdf"),
        ("video.mp4", "video/mp4"),
    ],
)
def test_incoming_original_archives_and_ranges(media, samples, name, mime):
    body = (samples / name).read_bytes()
    media.http.inbound["synthetic_media"] = body, mime
    attachment = media.service.capture_inbound(
        PHONE, "synthetic_event", "synthetic_media", mime, name, mime.split("/")[0]
    )
    assert attachment["availability"] == "ready"
    assert "storage_key" not in attachment and "meta_media_id" not in attachment
    assert not media.sent
    iterator, status, headers = media.service.content(
        PHONE, attachment["id"], range_header="bytes=0-7"
    )
    assert status == 206 and headers["Content-Range"] == f"bytes 0-7/{len(body)}"
    assert b"".join(iterator) == body[:8]
    iterator, status, _ = media.service.content(PHONE, attachment["id"])
    assert status == 200 and b"".join(iterator) == body


def test_expired_provider_url_is_renewed_and_original_is_retained(media, samples):
    body = (samples / "sound.ogg").read_bytes()
    media.http.inbound["voice"] = body, "audio/ogg"
    media.http.expire_once = True
    attachment = media.service.capture_inbound(
        PHONE, "voice_event", "voice", "audio/ogg", "voice.ogg", "audio"
    )
    assert attachment["availability"] == "ready"
    graph_reads = [
        url
        for method, url, _ in media.http.calls
        if method == "GET" and "graph.facebook.com" in url
    ]
    assert len(graph_reads) == 2
    iterator, _, headers = media.service.content(
        PHONE, attachment["id"], variant="playback"
    )
    assert headers["Content-Type"] == "audio/mp4" and b"".join(iterator)
    original, _, _ = media.service.content(PHONE, attachment["id"])
    assert b"".join(original) == body


def test_upload_is_not_send_and_command_replay_is_not_a_second_send(media, samples):
    identity = str(uuid4())
    first = upload(media, samples / "document.pdf", identity=identity)
    assert first.status_code == 200, first.text
    assert not media.sent
    assert (
        upload(media, samples / "document.pdf", identity=identity).json()["id"]
        == identity
    )
    body = {
        "actor": ACTOR,
        "command_id": "synthetic_send",
        "expected_version": 3,
        "attachment_id": identity,
        "text": "Documento",
    }
    response = media.client.post(
        media.base + "/messages", json=body, headers=media.headers
    )
    assert response.status_code == 200 and response.json()["status"] == "accepted"
    assert len(media.sent) == 1
    replay = media.client.post(
        media.base + "/messages", json=body, headers=media.headers
    )
    assert replay.status_code == 200 and replay.json()["status"] == "accepted"
    assert len(media.sent) == 1
    altered = media.client.post(
        media.base + "/messages", json={**body, "text": "Otra"}, headers=media.headers
    )
    assert altered.status_code == 409 and len(media.sent) == 1


def test_lost_control_upload_and_cross_scope_identifiers_are_rejected(
    media, samples, monkeypatch
):
    real = media.service.upload

    def changes_owner(*args, **kwargs):
        result = real(*args, **kwargs)
        media.store.owner = "another_operator"
        return result

    monkeypatch.setattr(media.service, "upload", changes_owner)
    response = upload(media, samples / "document.pdf")
    assert response.status_code == 409
    identity = next(iter(media.db.rows))[1]
    assert (
        media.client.get(
            media.base.replace(PHONE, "573000000002") + "/attachments/" + identity,
            headers=media.headers,
        ).status_code
        == 404
    )
    assert (
        media.client.get(
            media.base.replace("briosos", "tanaka") + "/attachments/" + identity,
            headers=media.headers,
        ).status_code
        == 403
    )
    assert (
        media.client.get(
            media.base + "/attachments/not-a-uuid", headers=media.headers
        ).status_code
        == 422
    )
    response = media.client.post(
        media.base + "/messages",
        json={
            "actor": ACTOR,
            "command_id": "stale",
            "expected_version": 3,
            "attachment_id": identity,
        },
        headers=media.headers,
    )
    assert response.status_code == 409 and not media.sent


def test_oversized_upload_and_real_type_mismatch(media, samples):
    assert (
        upload(
            media,
            samples / "document.pdf",
            headers={"X-Upload-Size": str(LIMITS["document"] + 1)},
        ).status_code
        == 413
    )
    assert (
        upload(media, samples / "image.png", mime="application/pdf").status_code == 415
    )
    assert not media.sent and not media.db.rows


def test_meta_upload_failure_is_confirmed_not_sent(media, samples):
    identity = upload(media, samples / "document.pdf").json()["id"]
    row = media.service.outbound(PHONE, identity, ACTOR)
    for mode in ("rejected", "timeout"):
        media.http.media_mode = mode
        with pytest.raises(ProviderError) as failure:
            media.service.send_attachment(
                PHONE,
                row,
                "",
                lambda *_: pytest.fail("No message may be sent after failed upload"),
            )
        assert failure.value.confirmed_not_sent
    assert not media.sent


def test_message_timeout_is_unknown_and_never_repeated(media, samples, monkeypatch):
    identity = upload(media, samples / "document.pdf").json()["id"]
    calls = []

    def ambiguous(*_args):
        calls.append(True)
        raise ProviderError(
            "meta", "send_document", None, message="synthetic ambiguous"
        )

    monkeypatch.setattr(media.service, "send_attachment", ambiguous)
    body = {
        "actor": ACTOR,
        "command_id": "ambiguous_media",
        "expected_version": 3,
        "attachment_id": identity,
    }
    for _ in range(2):
        response = media.client.post(
            media.base + "/messages", json=body, headers=media.headers
        )
        assert response.status_code == 202 and response.json()["status"] == "unknown"
    assert len(calls) == 1


def test_invalid_range_has_416_and_total_size(media, samples):
    attachment = upload(media, samples / "document.pdf").json()
    response = media.client.get(
        media.base + f"/attachments/{attachment['id']}/content",
        headers={**media.headers, "Range": "bytes=999999-"},
    )
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{attachment['size_bytes']}"
