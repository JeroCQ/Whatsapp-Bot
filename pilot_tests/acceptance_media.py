"""Cross-layer media acceptance: real bot/CRM routers, PG and local codecs.

Only Meta and private object storage are simulated. These tests never contact
customers, upload to a real bucket, or assert physical WhatsApp receipt.

Run from the repository root with its bot environment:
  PYTHONPATH=.:crm:tests CRM_TEST_DATABASE_URL=postgresql+psycopg://agent@127.0.0.1:55432/crm_test_media_api \
    .venv/bin/python -m pytest -q pilot_tests/acceptance_media.py
"""

import hashlib
import json
import types
from dataclasses import replace
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from pilot_tests import acceptance_pilot as base
from pilot_tests.acceptance_pilot import pilot as _pilot_fixture
from test_crm_media_policy import samples as _samples_fixture


pilot = _pilot_fixture
samples = _samples_fixture


class FakeResponse:
    def __init__(self, status=200, *, data=None, content=b"", headers=None):
        self.status_code, self._data, self.content = status, data, content
        self.headers = {"Content-Length": str(len(content)), **(headers or {})}
        self.closed = False

    def json(self):
        return self._data

    def iter_content(self, size):
        for offset in range(0, len(self.content), size):
            yield self.content[offset : offset + size]

    def close(self):
        self.closed = True


class SyntheticPrivateTransport:
    """In-memory providers behind the *real* archive/Meta transport code."""

    def __init__(self):
        self.objects, self.inbound, self.uploads = {}, {}, []
        self.calls = []
        self.expire_once = False
        self.after_store = None

    @staticmethod
    def _key(path):
        value = path.split("/storage/v1/object/", 1)[1]
        return unquote(value.removeprefix("authenticated/"))

    def get(self, url, *, headers, **kwargs):
        parsed = urlsplit(url)
        self.calls.append(("GET", parsed.hostname, parsed.path))
        if parsed.hostname == "synthetic-media-storage.invalid":
            assert headers["Authorization"] == "Bearer synthetic-private-storage-key"
            if "/storage/v1/bucket/" in parsed.path:
                return FakeResponse(data={"public": False})
            key = self._key(parsed.path)
            if key not in self.objects:
                return FakeResponse(404)
            content, mime = self.objects[key]
            if headers.get("Range"):
                start, end = map(
                    int, headers["Range"].removeprefix("bytes=").split("-")
                )
                return FakeResponse(
                    206,
                    content=content[start : end + 1],
                    headers={
                        "Content-Type": mime,
                        "Content-Range": f"bytes {start}-{end}/{len(content)}",
                    },
                )
            return FakeResponse(content=content, headers={"Content-Type": mime})
        if parsed.hostname == "graph.facebook.com":
            media_id = parsed.path.rsplit("/", 1)[-1]
            content, mime = self.inbound[media_id]
            return FakeResponse(
                data={
                    "url": "https://lookaside.fbsbx.com/" + media_id,
                    "mime_type": mime,
                    "file_size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        if parsed.hostname == "lookaside.fbsbx.com":
            if self.expire_once:
                self.expire_once = False
                return FakeResponse(403)
            content, mime = self.inbound[parsed.path.lstrip("/")]
            return FakeResponse(content=content, headers={"Content-Type": mime})
        raise AssertionError("External network is forbidden in acceptance tests")

    def post(self, url, *, headers, data, **kwargs):
        parsed = urlsplit(url)
        self.calls.append(("POST", parsed.hostname, parsed.path))
        if parsed.hostname == "synthetic-media-storage.invalid":
            assert headers["Authorization"] == "Bearer synthetic-private-storage-key"
            key = self._key(parsed.path)
            content = data.read()
            assert len(content) == int(headers["Content-Length"])
            self.objects[key] = content, headers["Content-Type"]
            if self.after_store:
                callback, self.after_store = self.after_store, None
                callback()
            return FakeResponse(200, data={"Key": key})
        if parsed.hostname == "graph.facebook.com" and parsed.path.endswith("/media"):
            # Media upload is not the message send. The latter goes through the
            # original bot sender and SyntheticMeta in the existing pilot fixture.
            self.uploads.append(data.read())
            return FakeResponse(
                data={"id": "synthetic_uploaded_media_" + str(len(self.uploads))}
            )
        raise AssertionError("External network is forbidden in acceptance tests")


@pytest.fixture
def media_pilot(pilot, monkeypatch):
    from crm_media import MediaService
    from crm_service.api import create_app

    monkeypatch.setattr(base.main.config, "CRM_MEDIA_ENABLED", False, raising=False)
    base._post_incoming(pilot, message_id="wamid.SYNTHETIC.pre-media")
    before = pilot.repository.table("message_logs").select("*").execute().data
    upgrade = (base.ROOT / "supabase/crm_media_upgrade.sql").read_text()
    with psycopg.connect(pilot.bot_dsn, autocommit=True) as connection:
        connection.execute(upgrade)
        connection.execute(upgrade)
    assert pilot.repository.table("message_logs").select("*").execute().data == before

    # Reuse the test Supabase repository against actual PG. UUID conversion is
    # transport serialization, matching the real PostgREST JSON response.
    previous_jsonable = base._jsonable
    monkeypatch.setattr(
        base,
        "_jsonable",
        lambda value: str(value)
        if isinstance(value, UUID)
        else previous_jsonable(value),
    )
    monkeypatch.setattr(
        base.SQLBotRepository,
        "TABLES",
        base.SQLBotRepository.TABLES | {"crm_media_assets"},
    )
    for key, value in {
        "CRM_MEDIA_ENABLED": True,
        "CRM_MEDIA_ALLOWED_BUSINESSES": "",
        "CRM_MEDIA_BUCKET": "crm-private-media",
        "SUPABASE_URL": "https://synthetic-media-storage.invalid",
        "SUPABASE_KEY": "synthetic-private-storage-key",
    }.items():
        monkeypatch.setattr(base.main.config, key, value, raising=False)
    storage = SyntheticPrivateTransport()
    archive = MediaService(base.main.config, pilot.repository, transport=storage)
    monkeypatch.setattr(base.main, "MediaService", lambda *_args, **_kwargs: archive)
    monkeypatch.setattr(base.main, "supabase", pilot.repository)

    settings = replace(pilot.settings, media_enabled=True, media_businesses=None)
    app = create_app(settings)
    app.state.bot_transport = pilot.transport
    try:
        with TestClient(app) as client:
            client.headers.update(base._jwt(settings))
            yield types.SimpleNamespace(
                **{
                    **vars(pilot),
                    "crm": client,
                    "app": app,
                    "settings": settings,
                    "archive": archive,
                    "storage": storage,
                }
            )
    finally:
        app.state.engine.dispose()


def synchronize(pilot):
    from crm_service.bot_source import BotSourceAdapter
    from crm_service.sync import Synchronizer

    worker = Synchronizer(
        pilot.settings,
        pilot.app.state.sessions,
        lambda business: BotSourceAdapter(business, transport=pilot.transport),
    )
    assert worker.run(base.BUSINESS)[base.BUSINESS] == "ok"


def upload(pilot, path, mime, *, voice_note=False):
    asset_id = str(uuid4())
    version = base._control(pilot)["version"]
    response = pilot.crm.post(
        f"/v1/conversations/{base.BUSINESS}/{base.PHONE}/attachments",
        params={
            "upload_id": asset_id,
            "filename": path.name,
            "expected_version": version,
            "voice_note": str(voice_note).lower(),
        },
        content=path.read_bytes(),
        headers={"Content-Type": mime, "X-Upload-Size": str(path.stat().st_size)},
    )
    return asset_id, version, response


@pytest.mark.parametrize(
    "filename,mime,kind",
    [
        ("image.png", "image/png", "image"),
        ("sound.ogg", "audio/ogg", "audio"),
        ("document.pdf", "application/pdf", "document"),
        ("video.mp4", "video/mp4", "video"),
    ],
)
def test_real_upload_send_receipts_update_original_without_resending(
    media_pilot, samples, filename, mime, kind
):
    from crm_service.models import SourceMessage

    pilot = media_pilot
    base._take(pilot)
    asset_id, version, stored = upload(pilot, samples / filename, mime)
    assert stored.status_code == 200, stored.text
    assert stored.json()["kind"] == kind and stored.json()["availability"] == "ready"
    assert len(pilot.meta.sent) == 0, "A stored file must not look like a WhatsApp send"
    command_id = "synthetic-media-send-" + kind
    body = {
        "command_id": command_id,
        "expected_version": version,
        "attachment_id": asset_id,
    }
    path = f"/v1/conversations/{base.BUSINESS}/{base.PHONE}"
    sent = pilot.crm.post(path + "/messages", json=body)
    assert sent.status_code == 200 and sent.json()["status"] == "accepted", sent.text
    assert len(pilot.meta.sent) == 1 and len(pilot.storage.uploads) == 1
    assert pilot.meta.sent[0]["payload"]["type"] == kind
    assert pilot.crm.post(path + "/messages", json=body).json()["status"] == "accepted"
    assert len(pilot.meta.sent) == 1 and len(pilot.storage.uploads) == 1
    synchronize(pilot)
    visible = pilot.crm.get(path).json()["messages"]
    original = next(item for item in visible if item["role"] == "asesor")
    assert original["attachments"][0]["id"] == asset_id
    assert original["delivery_status"] == "accepted"

    # Move the outgoing message outside the latest page before receipt arrival.
    pilot.repository.table("message_logs").insert(
        {
            "phone_number": base.PHONE,
            "role": "user",
            "content": "SYNTHETIC later message",
            "metadata": {"source_event_id": "synthetic-later"},
        }
    ).execute()
    synchronize(pilot)
    checkpoint = pilot.crm.get(path, params={"limit": 1}).json()["delivery_cursor"]
    for status in ("read", "delivered", "read", "sent"):
        encoded, headers = base._signed(base._receipt(pilot.meta.sent[0], status))
        assert (
            pilot.bot.post("/webhook", content=encoded, headers=headers).status_code
            == 200
        )
    synchronize(pilot)
    updates = pilot.crm.get(
        path + "/message-updates", params={"after": checkpoint}
    ).json()
    updated = next(item for item in updates["items"] if item["id"] == original["id"])
    assert updated["delivery_status"] == "read"
    detail = pilot.crm.get(path).json()
    assert all(
        not item["content"].startswith("DELIVERY_STATUS:")
        for item in detail["messages"]
    )
    assert any(item["role"] == "system" for item in detail["messages"])
    with pilot.app.state.sessions() as session:
        assert any(
            row.is_delivery_event
            for row in session.scalars(select(SourceMessage)).all()
        )
    content = pilot.crm.get(
        path + f"/attachments/{asset_id}/content", headers={"Range": "bytes=0-3"}
    )
    assert (
        content.status_code == 206
        and content.content == (samples / filename).read_bytes()[:4]
    )
    assert "private" not in json.dumps(updated["attachments"])


@pytest.mark.parametrize(
    "filename,mime,kind",
    [
        ("image.png", "image/png", "image"),
        ("sound.ogg", "audio/ogg", "audio"),
        ("document.pdf", "application/pdf", "document"),
        ("video.mp4", "video/mp4", "video"),
    ],
)
def test_inbound_original_archived_and_read_through_real_bot_and_crm(
    media_pilot, samples, filename, mime, kind
):
    pilot = media_pilot
    base._take(pilot)
    media_id = "synthetic_inbound_" + kind
    original = (samples / filename).read_bytes()
    pilot.storage.inbound[media_id] = original, mime
    if kind == "audio":
        pilot.storage.expire_once = True
    incoming = base._incoming("wamid.SYNTHETIC.media." + kind)
    message = incoming["entry"][0]["changes"][0]["value"]["messages"][0]
    message.pop("text")
    message["type"], message[kind] = (
        kind,
        {"id": media_id, "mime_type": mime, "filename": filename},
    )
    encoded, headers = base._signed(incoming)
    assert (
        pilot.bot.post("/webhook", content=encoded, headers=headers).status_code == 200
    )
    synchronize(pilot)
    path = f"/v1/conversations/{base.BUSINESS}/{base.PHONE}"
    detail = pilot.crm.get(path).json()
    item = next(item for item in detail["messages"] if item["attachments"])
    attachment = item["attachments"][0]
    assert attachment["kind"] == kind and attachment["availability"] == "ready"
    downloaded = pilot.crm.get(path + f"/attachments/{attachment['id']}/content")
    assert downloaded.status_code == 200 and downloaded.content == original
    assert not pilot.meta.sent, "Human control must stop automated responses"
    assert (
        pilot.crm.get(
            f"/v1/conversations/{base.OTHER_BUSINESS}/{base.PHONE}/attachments/{attachment['id']}"
        ).status_code
        == 403
    )


def test_control_lost_while_uploading_does_not_send_the_ready_file(
    media_pilot, samples
):
    pilot = media_pilot
    control = base._take(pilot)
    pilot.storage.after_store = lambda: pilot.store.apply_control(
        base.PHONE,
        "close",
        "close-during-upload",
        "pilot.employee.one",
        control["version"],
        {"reason": "SYNTHETIC close during upload"},
    )
    asset_id, version, response = upload(
        pilot, samples / "document.pdf", "application/pdf"
    )
    assert response.status_code == 409, response.text
    result = pilot.crm.post(
        f"/v1/conversations/{base.BUSINESS}/{base.PHONE}/messages",
        json={
            "command_id": "synthetic-send-after-control-loss",
            "expected_version": version,
            "attachment_id": asset_id,
        },
    )
    assert (
        result.status_code == 409 and not pilot.meta.sent and not pilot.storage.uploads
    )
    assert base._control(pilot)["phase"] == "ai"


@pytest.mark.parametrize(
    "mode,expected_status", [("ambiguous", "unknown"), ("rejected", "failed")]
)
def test_media_acknowledgement_timeout_never_repeats_the_send(
    media_pilot, samples, mode, expected_status
):
    pilot = media_pilot
    base._take(pilot)
    asset_id, version, response = upload(
        pilot, samples / "document.pdf", "application/pdf"
    )
    assert response.status_code == 200
    pilot.meta.mode = mode
    path = f"/v1/conversations/{base.BUSINESS}/{base.PHONE}"
    body = {
        "command_id": "synthetic-unknown-media",
        "expected_version": version,
        "attachment_id": asset_id,
    }
    for _ in range(2):
        result = pilot.crm.post(path + "/messages", json=body)
        assert result.status_code == (202 if expected_status == "unknown" else 200), (
            result.text
        )
        assert result.json()["status"] == expected_status, result.text
    assert len(pilot.meta.sent) == 1 and len(pilot.storage.uploads) == 1
    command = pilot.crm.get(path + "/commands/synthetic-unknown-media")
    assert command.json()["status"] == expected_status
    assert len(pilot.meta.sent) == 1
    synchronize(pilot)
    detail = pilot.crm.get(path).json()
    original = next(
        item
        for item in detail["messages"]
        if item.get("command_id") == body["command_id"]
    )
    assert original["delivery_status"] == expected_status
    assert original["attachments"][0]["id"] == asset_id
    assert detail["bucket"] == "awaiting_human"
