"""Private CRM media transport with a synthetic bot and disposable PostgreSQL."""

from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from crm_service.api import create_app
from crm_service.delivery import project_message
from crm_service.media import LIMITS, MAX_UPLOAD_BYTES, safe_attachment, upload_parameters
from crm_service.models import AuditEvent, CRMConversation, SourceConversation, SourceMessage
from crm_service.schemas import HumanMessageInput
from crm_service.settings import Business
from tests.test_api import token

PHONE = "CO.SyntheticMediaContact"
ASSET = "c658b335-788e-44b3-81be-8b18e1124c34"
DATA = b"%PDF-synthetic-private-content"
ACTOR = "synthetic.media.operator"
BASE = f"/v1/conversations/briosos/{PHONE}"


class SyntheticBot:
    def __init__(self):
        self.calls = []
        self.uploads = []
        self.versions = [7, 7]
        self.owner = ACTOR
        self.asset_business = "briosos"
        self.asset_phone = PHONE
        self.hash = "a" * 64
        self.storage_ready = True
        self.timeout_upload = False
        self.redirect_content = False
        self.timeout_send = False

    def control(self):
        version = self.versions.pop(0) if len(self.versions) > 1 else self.versions[0]
        return {
            "business_id": "briosos",
            "phone_number": PHONE,
            "provider": "crm",
            "phase": "human",
            "version": version,
            "owner": self.owner,
            "is_paused": True,
            "confirmed": True,
            "can_send": True,
            "window_expires_at": "2030-01-01T00:00:00Z",
        }

    def attachment(self):
        return {
            "business_id": self.asset_business,
            "phone_number": self.asset_phone,
            "id": ASSET,
            "kind": "document",
            "mime_type": "application/pdf",
            "filename": "synthetic.pdf",
            "size_bytes": len(DATA),
            "sha256": self.hash,
            "availability": "ready",
            "storage_key": "private-do-not-expose",
            "provider_url": "https://forbidden.invalid/token",
        }

    def __call__(self, request):
        assert request.url.host == "synthetic-briosos-bot.invalid"
        assert request.headers["x-crm-bot-secret"].startswith("synthetic-control-")
        assert "authorization" not in request.headers and "origin" not in request.headers
        self.calls.append(request)
        path = request.url.path
        if path.endswith("media-capabilities"):
            return httpx.Response(
                200,
                json={"business_id": "briosos", "media_enabled": True, "storage_ready": self.storage_ready},
            )
        if path.endswith("/control"):
            return httpx.Response(200, json=self.control())
        if path.endswith("/attachments"):
            self.uploads.append(request.read())
            assert request.headers["x-crm-actor"] == ACTOR
            assert int(request.headers["x-upload-size"]) == len(self.uploads[-1])
            assert request.url.params["upload_id"] == ASSET
            if self.timeout_upload:
                raise httpx.ReadTimeout("synthetic upload acknowledgement lost", request=request)
            return httpx.Response(200, json=self.attachment())
        if path.endswith("/content"):
            if self.redirect_content:
                return httpx.Response(307, headers={"Location": "https://forbidden.invalid/collect"})
            if request.headers.get("range"):
                assert request.headers["range"] == "bytes=0-3"
                assert request.url.params["variant"] == "original"
                return httpx.Response(
                    206,
                    content=DATA[:4],
                    headers={
                        "Content-Range": f"bytes 0-3/{len(DATA)}",
                        "Accept-Ranges": "bytes",
                        "Content-Type": "application/pdf",
                        "Content-Length": "4",
                    },
                )
            return httpx.Response(200, content=DATA, headers={"Content-Type": "application/pdf"})
        if "/attachments/" in path:
            return httpx.Response(200, json=self.attachment())
        if path.endswith("/messages"):
            if self.timeout_send:
                raise httpx.ReadTimeout("synthetic send acknowledgement lost", request=request)
            body = __import__("json").loads(request.read())
            return httpx.Response(
                200,
                json={
                    "command_id": body["command_id"],
                    "status": "accepted",
                    "provider_message_id": "synthetic-provider-message",
                    "control": self.control(),
                },
            )
        if "/commands/" in path:
            return httpx.Response(
                200,
                json={
                    "command_id": path.rsplit("/", 1)[-1],
                    "status": "unknown",
                    "provider_message_id": None,
                    "control": self.control(),
                },
            )
        raise AssertionError("Unexpected synthetic bot route: " + path)


@pytest.fixture
def media_client(settings, sessions):
    business = Business(
        "briosos",
        "",
        "",
        bot_api_url="https://synthetic-briosos-bot.invalid",
        bot_control_secret="synthetic-control-" + "x" * 40,
        operations_enabled=True,
    )
    settings = replace(
        settings,
        businesses={**settings.businesses, "briosos": business},
        media_enabled=True,
        media_businesses=("briosos",),
    )
    app = create_app(settings)
    bot = SyntheticBot()
    app.state.bot_transport = httpx.MockTransport(bot)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + token(
            settings, profile=ACTOR, allowed_businesses=["briosos"]
        )
        yield client, bot, settings
    app.state.engine.dispose()


def upload(client, *, content=DATA, **params):
    return client.post(
        BASE + "/attachments",
        params={"upload_id": ASSET, "filename": "synthetic.pdf", "expected_version": 7, **params},
        content=content,
        headers={"Content-Type": "application/pdf", "X-Upload-Size": str(len(content))},
    )


def test_raw_upload_is_private_and_is_not_a_whatsapp_send(media_client, sessions):
    client, bot, _ = media_client
    response = upload(client)
    assert response.status_code == 200
    result = response.json()
    assert result["id"] == ASSET and result["availability"] == "ready"
    assert "storage_key" not in result and "provider_url" not in result
    assert "accepted" not in result and bot.uploads == [DATA]
    assert not any(call.url.path.endswith("/messages") for call in bot.calls)
    with sessions() as session:
        assert session.scalar(select(AuditEvent).where(AuditEvent.action == "media.upload"))


def test_upload_control_is_rechecked_after_receiving_bytes(media_client):
    client, bot, _ = media_client
    bot.versions = [7, 8]
    assert upload(client).status_code == 409
    assert bot.uploads == []


@pytest.mark.parametrize(
    "params", [{"upload_id": "../secret"}, {"filename": "../secret.pdf"}, {"filename": "bad\nname.pdf"}]
)
def test_upload_rejects_manipulated_identities(media_client, params):
    client, bot, _ = media_client
    assert upload(client, **params).status_code == 422
    assert bot.calls == []


@pytest.mark.parametrize(
    "content_type,size,status",
    [
        ("image/jpeg", str(LIMITS["image"] + 1), 413),
        ("audio/ogg", str(LIMITS["audio"] + 1), 413),
        ("video/mp4", str(LIMITS["video"] + 1), 413),
        ("application/pdf", "bad", 422),
    ],
)
def test_invalid_upload_type_and_limits_are_rejected_before_storage(media_client, content_type, size, status):
    client, bot, _ = media_client
    response = client.post(
        BASE + "/attachments",
        params={"upload_id": ASSET, "filename": "bad.file", "expected_version": 7},
        content=b"fake",
        headers={"Content-Type": content_type, "X-Upload-Size": size},
    )
    assert response.status_code == status
    assert bot.calls == []


def test_actual_upload_size_and_body_limits_are_enforced(media_client):
    client, bot, _ = media_client
    params = {"upload_id": ASSET, "filename": "synthetic.pdf", "expected_version": 7}
    assert (
        client.post(
            BASE + "/attachments",
            params=params,
            content=DATA,
            headers={"Content-Type": "application/pdf", "X-Upload-Size": "1"},
        ).status_code
        == 413
    )
    assert (
        client.post(
            BASE + "/attachments",
            params=params,
            content=b"a",
            headers={"Content-Type": "application/pdf", "X-Upload-Size": "2"},
        ).status_code
        == 422
    )
    assert (
        client.post(BASE + "/messages", content=b"x", headers={"Content-Length": "2000000"}).status_code
        == 413
    )
    assert (
        client.post(
            BASE + "/attachments",
            params=params,
            content=b"x",
            headers={"Content-Length": str(MAX_UPLOAD_BYTES + 1)},
        ).status_code
        == 413
    )
    assert bot.uploads == []


def test_upload_timeout_is_not_accepted_and_has_no_automatic_retry(media_client):
    client, bot, _ = media_client
    bot.timeout_upload = True
    assert upload(client).status_code == 503
    assert len(bot.uploads) == 1
    assert not any(call.url.path.endswith("/messages") for call in bot.calls)


def test_private_download_supports_ranges_and_blocks_redirects(media_client):
    client, bot, _ = media_client
    response = client.get(BASE + f"/attachments/{ASSET}/content", headers={"Range": "bytes=0-3"})
    assert response.status_code == 206 and response.content == DATA[:4]
    assert response.headers["content-range"] == f"bytes 0-3/{len(DATA)}"
    assert response.headers["cache-control"] == "no-store"
    assert (
        client.get(BASE + f"/attachments/{ASSET}/content", headers={"Range": "bytes=0-1,4-8"}).status_code
        == 416
    )
    bot.redirect_content = True
    assert client.get(BASE + f"/attachments/{ASSET}/content").status_code == 503
    assert all(call.url.host == "synthetic-briosos-bot.invalid" for call in bot.calls)


@pytest.mark.parametrize("field,value", [("asset_business", "tanaka"), ("asset_phone", "another-contact")])
def test_metadata_cannot_be_rebound_to_another_scope(media_client, field, value):
    client, bot, _ = media_client
    setattr(bot, field, value)
    assert client.get(BASE + f"/attachments/{ASSET}").status_code == 502
    assert client.get(BASE + f"/attachments/{ASSET}/content").status_code == 502


def test_cross_business_auth_and_default_off(media_client):
    client, bot, settings = media_client
    assert client.get(f"/v1/conversations/tanaka/{PHONE}/attachments/{ASSET}").status_code == 403
    client.headers["Authorization"] = "Bearer invalid"
    assert client.get(BASE + f"/attachments/{ASSET}").status_code == 401
    app = create_app(replace(settings, media_enabled=False))
    app.state.bot_transport = httpx.MockTransport(
        lambda _: pytest.fail("disabled media must not contact bot")
    )
    with TestClient(app) as disabled:
        disabled.headers["Authorization"] = "Bearer " + token(
            settings, profile=ACTOR, allowed_businesses=["briosos"]
        )
        assert disabled.get(BASE + f"/attachments/{ASSET}").status_code == 404
    app.state.engine.dispose()


def test_capabilities_do_not_claim_ready_without_private_storage(media_client):
    client, bot, _ = media_client
    assert client.get("/v1/capabilities").json()["media_enabled"] is True
    bot.storage_ready = False
    value = client.get("/v1/capabilities").json()
    assert value["media_enabled"] is False and value["media"]["storage_ready"] is False


def test_one_unavailable_business_does_not_block_other_media_capabilities(media_client):
    _, bot, settings = media_client
    broken = Business("future_shop", "", "", operations_enabled=True)
    settings = replace(
        settings, businesses={**settings.businesses, "future_shop": broken}, media_businesses=None
    )
    app = create_app(settings)
    app.state.bot_transport = httpx.MockTransport(bot)
    with TestClient(app) as client:
        response = client.get(
            "/v1/capabilities",
            headers={
                "Authorization": "Bearer "
                + token(settings, profile=ACTOR, allowed_businesses=["briosos", "future_shop"])
            },
        )
        assert response.status_code == 200
        assert response.json()["media_businesses"] == ["briosos"]
        assert response.json()["media"]["unavailable_businesses"] == {"future_shop": "bot_media_unavailable"}
    app.state.engine.dispose()


def test_attachment_send_rechecks_control_and_pins_file_content(media_client):
    client, bot, _ = media_client
    body = {"command_id": "synthetic-attachment-send", "expected_version": 7, "attachment_id": ASSET}
    assert client.post(BASE + "/messages", json=body).json()["status"] == "accepted"
    assert client.post(BASE + "/messages", json=body).json()["status"] == "unknown"
    assert len([r for r in bot.calls if r.url.path.endswith("/messages")]) == 1
    bot.hash = "b" * 64
    assert client.post(BASE + "/messages", json=body).status_code == 409
    assert len([r for r in bot.calls if r.url.path.endswith("/messages")]) == 1


def test_ambiguous_media_send_is_recovered_only_with_get(media_client):
    client, bot, _ = media_client
    bot.timeout_send = True
    body = {"command_id": "synthetic-uncertain-media", "expected_version": 7, "attachment_id": ASSET}
    for _ in range(2):
        response = client.post(BASE + "/messages", json=body)
        assert response.status_code == 202 and response.json()["status"] == "unknown"
    assert len([r for r in bot.calls if r.url.path.endswith("/messages")]) == 1
    assert len([r for r in bot.calls if "/commands/" in r.url.path]) == 1


def test_message_requires_content_and_never_accepts_arbitrary_urls():
    for value in (
        {"command_id": "test", "expected_version": 1},
        {"command_id": "test", "expected_version": 1, "attachment_id": "../../file"},
        {"command_id": "test", "expected_version": 1, "url": "https://forbidden.invalid"},
    ):
        with pytest.raises(ValueError):
            HumanMessageInput.model_validate(value)


def test_unknown_browser_mime_is_only_a_hint_and_webp_stickers_are_supported():
    assert upload_parameters("", "1234", False) == ("application/octet-stream", 1234)
    assert upload_parameters("application/octet-stream", "1234", False) == ("application/octet-stream", 1234)
    assert upload_parameters("image/webp", "1000", False) == ("image/webp", 1000)
    asset = {
        "business_id": "briosos",
        "phone_number": PHONE,
        "id": ASSET,
        "kind": "sticker",
        "mime_type": "image/webp",
        "filename": "synthetic.webp",
        "size_bytes": 1234,
        "sha256": "a" * 64,
        "availability": "ready",
    }
    assert safe_attachment(asset, "briosos", PHONE, ASSET)["kind"] == "sticker"
    assert (
        safe_attachment(
            {**asset, "kind": "document", "mime_type": "application/octet-stream"}, "briosos", PHONE, ASSET
        )["mime_type"]
        == "application/octet-stream"
    )
    assert upload_parameters("application/x-browser-unknown", "1234", False) == (
        "application/octet-stream",
        1234,
    )


def test_delivery_updates_reach_a_message_outside_the_latest_page(media_client, sessions):
    client, _, _ = media_client
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with sessions.begin() as session:
        session.add(SourceConversation(business_id="briosos", phone_number=PHONE, name="Synthetic"))
        session.flush()
        session.add(
            CRMConversation(
                business_id="briosos",
                phone_number=PHONE,
                bucket="human_active",
                sort_key="synthetic",
                classification={"last_message_id": 4},
            )
        )
        old = SourceMessage(
            business_id="briosos",
            source_id=1,
            phone_number=PHONE,
            role="asesor",
            content="Holi",
            created_at=at,
            message_metadata={"command_id": "old-command", "delivery_status": "accepted"},
        )
        session.add(old)
        session.flush()
        project_message(session, old)
        for id, role, content in [
            (2, "user", "Next"),
            (3, "system", "HANDOFF: useful"),
            (4, "system", "CONTROL: useful"),
        ]:
            session.add(
                SourceMessage(
                    business_id="briosos",
                    source_id=id,
                    phone_number=PHONE,
                    role=role,
                    content=content,
                    created_at=at,
                    message_metadata={},
                )
            )
    first = client.get(BASE + "?limit=1").json()
    assert first["messages"][0]["id"] == 4
    cursor = first["delivery_cursor"]
    with sessions.begin() as session:
        event = SourceMessage(
            business_id="briosos",
            source_id=5,
            phone_number=PHONE,
            role="system",
            content="DELIVERY_STATUS: delivered",
            created_at=at,
            message_metadata={
                "source": "crm_delivery",
                "command_id": "old-command",
                "delivery_status": "delivered",
            },
        )
        session.add(event)
        session.flush()
        project_message(session, event)
    updates = client.get(BASE + "/message-updates", params={"after": cursor}).json()
    assert [(row["id"], row["delivery_status"]) for row in updates["items"]] == [(1, "delivered")]
    detail = client.get(BASE).json()
    assert [row["id"] for row in detail["messages"]] == [1, 2, 3, 4]
    assert (
        client.get(BASE + "/message-updates", params={"after": updates["next_cursor"]}).json()["items"] == []
    )
    with sessions() as session:
        assert session.get(SourceMessage, ("briosos", 5)).content == "DELIVERY_STATUS: delivered"
