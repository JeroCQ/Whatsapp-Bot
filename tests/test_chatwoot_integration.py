import hashlib
import hmac
import json
import os
import sys
import types

import pytest
from fastapi.testclient import TestClient

os.environ["BUSINESS_ID"] = "client_1"
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("WA_VERIFY_TOKEN", "verify")
os.environ.setdefault("WA_TOKEN", "wa-secret-token")
os.environ.setdefault("WA_PHONE_NUMBER_ID", "phone-id")
os.environ.setdefault("GEMINI_API_KEY", "gemini")
os.environ.setdefault("DASHBOARD_API_KEY", "dashboard-secret")
os.environ.setdefault("CHATWOOT_BASE_URL", "https://app.chatwoot.com")
os.environ.setdefault("CHATWOOT_API_TOKEN", "chatwoot-secret-token")
os.environ.setdefault("CHATWOOT_ACCOUNT_ID", "10")
os.environ.setdefault("CHATWOOT_INBOX_ID", "20")
os.environ.setdefault("CHATWOOT_ASSIGNMENT_MODE", "automatic")
os.environ.setdefault("CHATWOOT_WEBHOOK_SECRET", "webhook-secret")

fake_bot = types.ModuleType("bot")
fake_bot.FILE_CATALOG = {}
fake_bot.process_message_logic = lambda *args: None
fake_bot.transcribe_audio_message = lambda *args: None
sys.modules.setdefault("bot", fake_bot)

import main
import chatwoot_api
from provider_errors import ProviderError


def payload(account=10, inbox=20, event="message_created"):
    return {"event": event, "id": 91, "account": {"id": account}, "inbox": {"id": inbox},
            "conversation": {"id": 44, "inbox_id": inbox, "account": {"id": account}},
            "message_type": "outgoing", "private": False, "content": "agent reply"}


def signed(body: bytes, secret="webhook-secret"):
    timestamp = "2000000000"
    signature = "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"X-Chatwoot-Timestamp": timestamp, "X-Chatwoot-Signature": signature, "Content-Type": "application/json"}


def test_webhook_auth_scope_precedes_idempotency(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main.time, "time", lambda: 2000000000)
    claimed = []
    monkeypatch.setattr(main, "claim_webhook_event", lambda *args: claimed.append(args) or True)
    for headers, expected in [({}, 401), ({"X-Chatwoot-Timestamp": "x", "X-Chatwoot-Signature": "bad"}, 401)]:
        assert client.post("/chatwoot-webhook", content=json.dumps(payload()), headers=headers).status_code == expected
    for body_data in (payload(account=11), payload(inbox=21)):
        body = json.dumps(body_data).encode()
        assert client.post("/chatwoot-webhook", content=body, headers=signed(body)).status_code == 403
    assert claimed == []


def test_valid_webhook_and_root_dispatcher_constraint(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main.time, "time", lambda: 2000000000)
    monkeypatch.setattr(main, "claim_webhook_event", lambda *args: True)
    monkeypatch.setattr(main, "_dispatch_before_ack", lambda *args, **kwargs: "queued")
    body = json.dumps(payload()).encode()
    assert client.post("/chatwoot-webhook", content=body, headers=signed(body)).status_code == 200
    assert client.post("/", json=payload()).status_code == 404


def test_api_inbox_signing_secret_is_accepted_without_changing_assignment(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main.time, "time", lambda: 2000000000)
    monkeypatch.setattr(main.config, "CHATWOOT_API_INBOX_WEBHOOK_SECRET", "api-inbox-secret")
    monkeypatch.setattr(main, "_dispatch_before_ack", lambda *args, **kwargs: "queued")
    body = json.dumps(payload()).encode()

    assert client.post("/chatwoot-webhook", content=body, headers=signed(body, "api-inbox-secret")).status_code == 200
    assert main.config.CHATWOOT_ASSIGNMENT_MODE == "automatic"


def test_temporary_webhook_alias_uses_same_signature_validation(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main.time, "time", lambda: 2000000000)
    monkeypatch.setattr(main, "claim_webhook_event", lambda *args: True)
    monkeypatch.setattr(main, "_dispatch_before_ack", lambda *args, **kwargs: "queued")
    body = json.dumps(payload()).encode()

    assert client.post("/chatwoo-webhook", content=body, headers=signed(body)).status_code == 200
    assert client.post("/chatwoo-webhook", content=body).status_code == 401


def _mock_handoff_dependencies(monkeypatch):
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda phone: {"chatwoot_conversation_id": None})
    monkeypatch.setattr(main, "update_chatwoot_conversation_id", lambda *args: None)
    monkeypatch.setattr(main, "get_message_logs", lambda *args, **kwargs: [{"role": "user", "content": "Necesito ayuda"}])
    monkeypatch.setattr(main, "save_message_log", lambda *args: None)
    monkeypatch.setattr(chatwoot_api, "get_or_create_contact", lambda *args, **kwargs: 12)
    monkeypatch.setattr(chatwoot_api, "create_conversation", lambda contact_id: 34)


def test_handoff_summary_and_text_alert_are_private(monkeypatch):
    _mock_handoff_dependencies(monkeypatch)
    messages = []
    monkeypatch.setattr(chatwoot_api, "send_message_to_chatwoot", lambda *args, **kwargs: messages.append((args, kwargs)))

    main._create_handoff_ticket_if_needed(
        "57300", "Cliente", {"is_paused": True, "handoff_reason": "Necesita asesor"}, None, "", None, None
    )

    assert "Resumen" in messages[0][0][1]
    assert "**Motivo de transferencia:** Necesita asesor" in messages[0][0][1]
    assert messages[0][1]["is_private"] is True
    assert "🔔 Necesita asesor" in messages[1][0][1]
    assert messages[1][1]["is_private"] is True


def test_catalog_delivery_failure_pauses_and_creates_explicit_handoff(monkeypatch):
    state = {"is_paused": False, "chatwoot_conversation_id": None}
    turn = type("Turn", (), {
        "send_files_before_response": False,
        "requested_files": ["catalogo_portafolio"],
        "response": "Te comparto el catálogo.",
        "follow_up_message": "",
        "follow_up_delay_minutes": 120,
    })()
    logs = []
    customer_messages = []
    handoffs = []
    monkeypatch.setattr(main, "invalidate_follow_up", lambda *_args: None)
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *_args: dict(state))
    monkeypatch.setattr(main, "process_message_logic", lambda *_args: turn)
    monkeypatch.setattr(main, "deliver_presaved_file", lambda *_args: (
        False, "catalogo_portafolio: FileDeliveryError: meta_media_upload_failed"
    ))
    monkeypatch.setattr(main, "send_whatsapp_message", lambda _phone, text: customer_messages.append(text) or True)
    monkeypatch.setattr(main, "save_message_log", lambda *args: logs.append(args))
    monkeypatch.setattr(main, "pause_bot_for_handoff", lambda _phone, reason: state.update(
        is_paused=True, handoff_reason=reason
    ))
    monkeypatch.setattr(main, "_create_handoff_ticket_if_needed", lambda *args: handoffs.append(args))
    monkeypatch.setattr(main, "schedule_follow_up", lambda *_args: pytest.fail("must not schedule after handoff"))

    main._process_whatsapp_message_unlocked("57300", "Cliente", "Mándame el catálogo")

    assert state["is_paused"] is True
    assert "No se pudo entregar el catálogo" in state["handoff_reason"]
    assert any("Detalle técnico" in entry[2] and "meta_media_upload_failed" in entry[2] for entry in logs)
    assert customer_messages[-1] == (
        "Perdón, no pude adjuntar el catálogo. Ya avisé al equipo para que te lo envíe manualmente."
    )
    assert handoffs and handoffs[0][2]["is_paused"] is True


def test_authenticated_manual_handoff_creates_chatwoot_conversation(monkeypatch):
    state = {"is_paused": False, "chatwoot_conversation_id": None}
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *_args: dict(state))
    monkeypatch.setattr(main, "pause_bot_for_handoff", lambda _phone, reason: state.update(
        is_paused=True, handoff_reason=reason
    ))

    def create_ticket(*_args):
        state["chatwoot_conversation_id"] = 456

    monkeypatch.setattr(main, "_create_handoff_ticket_if_needed", create_ticket)
    client = TestClient(main.app)
    response = client.post(
        "/api/manual-handoff?client_name=client_1",
        headers={"X-Dashboard-API-Key": "dashboard-secret"},
        json={
            "phone_number": "573001112233",
            "customer_name": "Cliente",
            "reason": "Recuperar catálogo no entregado",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "status": "created", "conversation_id": 456}


def test_handoff_summary_and_media_alert_are_private(monkeypatch):
    _mock_handoff_dependencies(monkeypatch)
    events = []
    monkeypatch.setattr(chatwoot_api, "download_meta_media", lambda media_id: (b"file", "image/png"))
    monkeypatch.setattr(chatwoot_api, "send_message_to_chatwoot", lambda *args, **kwargs: events.append(("text", args, kwargs)))
    monkeypatch.setattr(chatwoot_api, "send_media_to_chatwoot", lambda *args, **kwargs: events.append(("media", args, kwargs)))

    main._create_handoff_ticket_if_needed(
        "57300", "Cliente", {"is_paused": True, "handoff_reason": "Revisar archivo"}, "media-1", "", "image/png", "foto.png"
    )

    assert events[0][0] == "text"
    assert "Resumen" in events[0][1][1]
    assert events[0][2]["is_private"] is True
    assert events[1][0] == "media"
    assert "🔔 Revisar archivo" in events[1][1][1]
    assert events[1][2]["is_private"] is True


def test_audio_handoff_reuses_transcription_download_and_uploads_playable_private_audio(monkeypatch):
    _mock_handoff_dependencies(monkeypatch)
    uploads = []
    monkeypatch.setattr(
        chatwoot_api, "download_meta_media",
        lambda media_id: pytest.fail("handoff downloaded audio a second time"),
    )
    monkeypatch.setattr(chatwoot_api, "send_message_to_chatwoot", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        chatwoot_api, "_prepare_audio_for_chatwoot",
        lambda data, mime: (b"converted-mp3", "audio/mpeg", ".mp3"),
    )
    monkeypatch.setattr(
        chatwoot_api, "send_media_to_chatwoot",
        lambda *args, **kwargs: uploads.append((args, kwargs)) or Response(200),
    )

    main._create_handoff_ticket_if_needed(
        "57300", "Cliente", {"is_paused": True, "handoff_reason": "Escuchar audio"},
        "audio-1", "transcripción", "audio/ogg; codecs=opus", None,
        effective_media_type="audio", media_bytes=b"original-ogg",
        downloaded_mime_type="audio/ogg; codecs=opus",
    )

    assert uploads[0][0][2] == b"converted-mp3"
    assert uploads[0][0][3] == "audio/mpeg"
    assert uploads[0][0][4].endswith(".mp3")
    assert uploads[0][0][5] is True


def test_audio_conversion_failure_preserves_original_and_visibility(monkeypatch):
    uploads = []
    notices = []
    monkeypatch.setattr(
        chatwoot_api, "_prepare_audio_for_chatwoot",
        lambda *args: (_ for _ in ()).throw(OSError("ffmpeg unavailable")),
    )
    monkeypatch.setattr(
        chatwoot_api, "send_media_to_chatwoot",
        lambda *args, **kwargs: uploads.append((args, kwargs)) or Response(200),
    )
    monkeypatch.setattr(
        chatwoot_api, "send_message_to_chatwoot",
        lambda *args, **kwargs: notices.append((args, kwargs)),
    )

    chatwoot_api.send_audio_to_chatwoot(
        34, b"original-ogg", "audio/ogg; codecs=opus", content="voice", is_private=False
    )

    assert uploads[0][0][2:4] == (b"original-ogg", "audio/ogg")
    assert uploads[0][0][4].startswith("nota_de_voz_original.")
    assert uploads[0][0][5] is False
    assert "reproducción en línea puede no estar disponible" in notices[0][0][1]
    assert notices[0][1]["is_private"] is False


def test_audio_conversion_uses_bundled_ffmpeg(monkeypatch):
    command = []
    monkeypatch.setattr(chatwoot_api.imageio_ffmpeg, "get_ffmpeg_exe", lambda: "/bundled/ffmpeg")
    monkeypatch.setattr(
        chatwoot_api.subprocess, "run",
        lambda args, **kwargs: command.extend(args) or types.SimpleNamespace(stdout=b"mp3"),
    )

    prepared, mime_type, extension = chatwoot_api._prepare_audio_for_chatwoot(
        b"ogg-opus", "audio/ogg; codecs=opus"
    )

    assert command[0] == "/bundled/ffmpeg"
    assert prepared == b"mp3"
    assert mime_type == "audio/mpeg"
    assert extension == ".mp3"


def test_paused_conversation_routes_audio_through_audio_uploader(monkeypatch):
    sent = []
    monkeypatch.setattr(main, "invalidate_follow_up", lambda *args: None)
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *args: {"is_paused": True, "chatwoot_conversation_id": 44})
    monkeypatch.setattr(main, "save_message_log", lambda *args: None)
    monkeypatch.setattr(chatwoot_api, "download_meta_media", lambda media_id: (b"ogg", "audio/ogg; codecs=opus"))
    monkeypatch.setattr(chatwoot_api, "send_media_to_chatwoot", lambda *args, **kwargs: pytest.fail("generic uploader used"))
    monkeypatch.setattr(chatwoot_api, "send_audio_to_chatwoot", lambda *args, **kwargs: sent.append((args, kwargs)))

    main._process_whatsapp_message_unlocked(
        "57300", "Cliente", "", media_id="audio-1", is_audio=True,
        audio_media_id="audio-1", media_type="audio", mime_type="audio/ogg; codecs=opus",
    )

    assert sent[0][0][0:3] == (44, b"ogg", "audio/ogg; codecs=opus")
    assert sent[0][1]["is_private"] is False


class Response:
    def __init__(self, status, body=None, headers=None, chunks=None):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}
        self._chunks = chunks or []
        self.content = b"".join(self._chunks)
    def json(self): return self._body
    def raise_for_status(self):
        if self.status_code >= 400: raise main.requests.HTTPError(response=self)
    def iter_content(self, size): return iter(self._chunks)
    def close(self): pass


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
def test_meta_failures_raise_and_redact(monkeypatch, capsys, status):
    response = Response(status, {"error": {"code": 190, "error_subcode": 7, "message": "bad wa-secret-token Authorization: Bearer leak"}})
    monkeypatch.setattr(main, "post", lambda *args, **kwargs: response)
    with pytest.raises(ProviderError) as caught:
        main.send_whatsapp_message("57300", "hello")
    print(caught.value)
    output = capsys.readouterr().out
    assert "wa-secret-token" not in output and "Bearer leak" not in output


def test_meta_200_returns_success(monkeypatch):
    monkeypatch.setattr(main, "post", lambda *args, **kwargs: Response(200))
    assert main.send_whatsapp_message("57300", "hello") is True


def test_failed_primary_response_suppresses_followup(monkeypatch):
    turn = type("Turn", (), {"send_files_before_response": False, "requested_files": [], "response": "reply",
                              "follow_up_message": "later", "follow_up_delay_minutes": 10})()
    monkeypatch.setattr(main, "invalidate_follow_up", lambda *a: None)
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *a: {"is_paused": False, "chatwoot_conversation_id": None})
    monkeypatch.setattr(main, "process_message_logic", lambda *a: turn)
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *a: (_ for _ in ()).throw(ProviderError("meta", "send", 400)))
    scheduled = []
    logged = []
    monkeypatch.setattr(main, "schedule_follow_up", lambda *a: scheduled.append(a))
    monkeypatch.setattr(main, "save_message_log", lambda *a: logged.append(a))
    with pytest.raises(ProviderError):
        main._process_whatsapp_message_unlocked("57300", "Name", "hello")
    assert scheduled == []
    assert not any(entry[1] == "model" for entry in logged)


def test_followup_at_or_after_24_hours_is_cancelled(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(main, "queue_enabled", lambda: True)
    monkeypatch.setattr(main, "follow_up_delay_seconds", lambda minutes: 24 * 60 * 60)
    monkeypatch.setattr(main, "invalidate_follow_up", lambda phone: calls.append(("cancel", phone)))
    monkeypatch.setattr(main, "register_follow_up", lambda *a: pytest.fail("must not register"))
    monkeypatch.setattr(main, "enqueue_in", lambda *a, **k: pytest.fail("must not enqueue"))

    main.schedule_follow_up("57300", "¿Sigues interesado?", 2880)

    assert calls == [("cancel", "57300")]
    assert "fuera de la ventana de 24 horas" in capsys.readouterr().out


def test_agent_text_forwarding_is_scoped(monkeypatch):
    sent = []
    statuses = []
    monkeypatch.setattr(main, "get_phone_by_chatwoot_id", lambda value: "57300")
    monkeypatch.setattr(main, "save_message_log", lambda *a: None)
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *a: sent.append(a) or True)
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *a, **k: None)
    monkeypatch.setattr(chatwoot_api, "update_message_status", lambda *a: statuses.append(a))
    main.process_chatwoot_event(payload())
    assert sent == [("57300", "agent reply")]
    assert statuses == [(44, 91, "delivered")]
    sent.clear()
    main.process_chatwoot_event(payload(account=99))
    assert sent == []


def test_string_false_private_flag_is_forwarded(monkeypatch):
    event = payload()
    event["private"] = "false"
    sent = []
    monkeypatch.setattr(main, "get_phone_by_chatwoot_id", lambda value: "57300")
    monkeypatch.setattr(main, "save_message_log", lambda *a: None)
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *a: sent.append(a) or True)
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *a, **k: None)
    monkeypatch.setattr(chatwoot_api, "update_message_status", lambda *a: None)

    main.process_chatwoot_event(event, event_id="message_created:91")

    assert sent == [("57300", "agent reply")]


@pytest.mark.parametrize(
    ("changes", "reason"),
    [({"private": True}, "reason=private_note"), ({"message_type": "incoming"}, "reason=message_type_incoming")],
)
def test_non_forwarded_chatwoot_message_logs_reason(monkeypatch, capsys, changes, reason):
    event = payload()
    event.update(changes)
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *a, **k: None)
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *a: pytest.fail("message must not be forwarded"))

    main.process_chatwoot_event(event, event_id="message_created:91")

    assert reason in capsys.readouterr().out


def test_unmapped_public_reply_logs_delivery_warning(monkeypatch, capsys):
    statuses = []
    monkeypatch.setattr(main, "get_phone_by_chatwoot_id", lambda value: None)
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *a, **k: None)
    monkeypatch.setattr(chatwoot_api, "update_message_status", lambda *a: statuses.append(a))

    main.process_chatwoot_event(payload(), event_id="message_created:91")

    output = capsys.readouterr().out
    assert "reason=conversation_not_mapped" in output
    assert "event_id=message_created:91" in output
    assert statuses == [(44, 91, "failed", "No se encontró el teléfono asociado a la conversación")]


def test_agent_attachment_forwarding(monkeypatch):
    event = payload()
    event["attachments"] = [{"data_url": "/rails/active_storage/file.png", "content_type": "image/png", "file_name": "proof.png"}]
    sent = []
    monkeypatch.setattr(main, "get_phone_by_chatwoot_id", lambda value: "57300")
    monkeypatch.setattr(main, "save_message_log", lambda *a: None)
    monkeypatch.setattr(main, "upload_chatwoot_attachment_to_meta", lambda *a: "media-1")
    monkeypatch.setattr(main, "send_whatsapp_media", lambda *a: sent.append(a) or True)
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *a, **k: None)
    monkeypatch.setattr(chatwoot_api, "update_message_status", lambda *a: None)
    main.process_chatwoot_event(event)
    assert sent == [("57300", "media-1", "image", "agent reply", "proof.png")]


def test_chatwoot_provider_log_redacts_api_token(monkeypatch, capsys):
    response = Response(401, {"error": {"message": "bad chatwoot-secret-token api_access_token=leak"}})
    monkeypatch.setattr(chatwoot_api, "post", lambda *a, **k: response)
    assert chatwoot_api.create_conversation(77) is None
    output = capsys.readouterr().out
    assert "chatwoot-secret-token" not in output and "api_access_token=leak" not in output


def test_relative_attachment_streams(monkeypatch):
    downloads = []
    def fake_get(url, **kwargs):
        downloads.append((url, kwargs.get("headers")))
        return Response(200, headers={"Content-Type": "image/png", "Content-Length": "3"}, chunks=[b"abc"])
    monkeypatch.setattr(main, "get", fake_get)
    monkeypatch.setattr(main, "post", lambda *a, **k: Response(200, {"id": "media-1"}))
    assert main.upload_chatwoot_attachment_to_meta("/rails/active_storage/file.png") == "media-1"
    assert downloads[0][0].startswith("https://app.chatwoot.com/")
    assert main.upload_chatwoot_attachment_to_meta("https://evil.example/file") is None
    assert len(downloads) == 1


def test_cross_origin_storage_redirect_does_not_leak_chatwoot_credentials(monkeypatch):
    calls = []
    def fake_get(url, **kwargs):
        calls.append((url, kwargs.get("headers")))
        if len(calls) == 1:
            return Response(302, headers={"Location": "https://storage.example/file"})
        return Response(200, headers={"Content-Type": "image/png"}, chunks=[b"abc"])
    monkeypatch.setattr(main, "get", fake_get)
    monkeypatch.setattr(main, "post", lambda *a, **k: Response(200, {"id": "media-1"}))
    assert main.upload_chatwoot_attachment_to_meta("/file") == "media-1"
    assert calls == [
        ("https://app.chatwoot.com/file", {"api_access_token": "chatwoot-secret-token"}),
        ("https://storage.example/file", {}),
    ]


def test_attachment_forwarding_failure_is_retriable(monkeypatch):
    event = payload()
    event["attachments"] = [{"data_url": "/file.png", "content_type": "image/png"}]
    marked = []
    statuses = []
    monkeypatch.setattr(main, "get_phone_by_chatwoot_id", lambda value: "57300")
    monkeypatch.setattr(main, "save_message_log", lambda *a: None)
    monkeypatch.setattr(main, "upload_chatwoot_attachment_to_meta", lambda *a: None)
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *a, **k: marked.append((a, k)))
    monkeypatch.setattr(chatwoot_api, "update_message_status", lambda *a: statuses.append(a))

    with pytest.raises(ProviderError):
        main.process_chatwoot_event(event, event_id="message_created:91")
    assert marked[-1][1]["status"] == "failed"
    assert statuses == [(44, 91, "failed", "No se pudo entregar el mensaje a WhatsApp")]


def test_chatwoot_api_message_status_uses_api_inbox_update_endpoint(monkeypatch):
    captured = {}

    def fake_put(url, **kwargs):
        captured.update(url=url, json=kwargs["json"])
        return Response(200, {"id": 91, "status": "delivered"})

    monkeypatch.setattr(chatwoot_api, "put", fake_put)

    assert chatwoot_api.update_message_status(44, 91, "delivered") is not None
    assert captured == {
        "url": "https://app.chatwoot.com/api/v1/accounts/10/conversations/44/messages/91",
        "json": {"status": "delivered"},
    }


def test_attachment_size_limit(monkeypatch):
    monkeypatch.setattr(main.config, "CHATWOOT_MAX_ATTACHMENT_BYTES", 2)
    monkeypatch.setattr(main, "get", lambda *a, **k: Response(200, headers={"Content-Length": "3"}, chunks=[b"abc"]))
    assert main.upload_chatwoot_attachment_to_meta("/file") is None


def test_account_path_and_configured_inbox_creation(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(chatwoot_api.config, "CHATWOOT_ASSIGNMENT_MODE", "fixed")
    monkeypatch.setattr(chatwoot_api.config, "CHATWOOT_ASSIGNEE_ID", "31")

    def fake_post(url, **kwargs):
        captured.update(url=url, json=kwargs["json"])
        return Response(200, {"id": 88, "meta": {"assignee": {"id": 31}}})

    monkeypatch.setattr(chatwoot_api, "post", fake_post)
    assert chatwoot_api.create_conversation(77) == 88
    assert captured["url"] == "https://app.chatwoot.com/api/v1/accounts/10/conversations"
    assert captured["json"] == {
        "inbox_id": 20,
        "contact_id": 77,
        "status": "open",
        "assignee_id": 31,
    }
    assert "assignment_mode=fixed requested_assignee_id=31 response_assignee_id=31" in capsys.readouterr().out


@pytest.mark.parametrize("rollback_assignee", [None, "31"])
def test_automatic_conversation_creation_uses_inbox_policy(monkeypatch, capsys, rollback_assignee):
    captured = {}
    monkeypatch.setattr(chatwoot_api.config, "CHATWOOT_ASSIGNMENT_MODE", "automatic")
    monkeypatch.setattr(chatwoot_api.config, "CHATWOOT_ASSIGNEE_ID", rollback_assignee)
    monkeypatch.setattr(
        chatwoot_api,
        "post",
        lambda url, **kwargs: captured.update(json=kwargs["json"]) or Response(200, {"id": 88}),
    )

    assert chatwoot_api.create_conversation(77) == 88
    assert "assignee_id" not in captured["json"]
    assert "assignment_mode=automatic requested_assignee_id=inbox-policy" in capsys.readouterr().out


def test_resolved_event_supports_nested_conversation_status(monkeypatch):
    event = payload(event="conversation_status_changed")
    event.pop("status", None)
    event["conversation"]["status"] = "resolved"
    resumed = []
    sent = []
    monkeypatch.setattr(main, "resume_bot_state", lambda conv_id: resumed.append(conv_id) or "57300")
    monkeypatch.setattr(main, "save_message_log", lambda *args: None)
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *args: sent.append(args) or True)
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *args, **kwargs: None)

    main.process_chatwoot_event(event, event_id="resolved:44")

    assert resumed == [44]
    assert sent == [("57300", "✅ Tu solicitud ha sido resuelta. Si necesitas algo más, envíame un mensaje.")]


def test_resolved_event_uses_warm_tanaka_closing(monkeypatch):
    event = payload(event="conversation_status_changed")
    event["status"] = "resolved"
    resumed = []
    sent = []
    monkeypatch.setattr(main.config, "BUSINESS_ID", "tanaka")
    monkeypatch.setattr(main, "resume_bot_state", lambda conv_id: resumed.append(conv_id) or "57300")
    monkeypatch.setattr(main, "save_message_log", lambda *args: None)
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *args: sent.append(args) or True)
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *args, **kwargs: None)

    main.process_chatwoot_event(event, event_id="resolved:tanaka:44")

    assert resumed == [44]
    assert sent == [
        ("57300", "Gracias por elegirnos, quedo por aquí super pendiente de lo que necesites ☺️")
    ]


def test_image_catalog_is_uploaded_and_sent_as_whatsapp_image(monkeypatch):
    sent_payloads = []
    item = types.SimpleNamespace(
        media_id=None,
        link="https://placeholder.test/catalog.pdf",
        filename="Catálogo Memo's.pdf",
        media_type="document",
        default_caption="Catálogo",
    )

    def fake_head(url, **kwargs):
        if url.endswith(".png"):
            return Response(200, headers={"Content-Type": "image/png", "ETag": "v1"})
        return Response(404)

    monkeypatch.setattr(main.requests, "head", fake_head)
    monkeypatch.setattr(main, "FILE_CATALOG", {"catalogo_pdf": item})
    monkeypatch.setattr(main, "upload_public_url_to_meta_media", lambda url, filename, content_type: "media-png")
    monkeypatch.setattr(main, "post", lambda url, **kwargs: sent_payloads.append(kwargs["json"]) or Response(200))

    assert main.send_presaved_file("57300", "catalogo_pdf") is True
    assert sent_payloads[0]["type"] == "image"
    assert sent_payloads[0]["image"]["id"] == "media-png"
    assert sent_payloads[0]["image"]["caption"] == "Catálogo Memo's"
    assert "filename" not in sent_payloads[0]["image"]


def test_pdf_catalog_uses_commercial_name_but_upload_keeps_cache_buster(monkeypatch):
    sent_payloads = []
    uploaded_filenames = []
    item = types.SimpleNamespace(
        media_id=None,
        link="https://placeholder.test/catalog.png",
        filename="Catálogo Tanaka.png",
        media_type="image",
        default_caption="Aquí tienes nuestro catálogo completo ☺️",
    )

    def fake_head(url, **kwargs):
        if url.endswith(".pdf"):
            return Response(200, headers={"Content-Type": "application/pdf", "ETag": "pdf-v1"})
        return Response(404)

    def fake_upload(url, filename, content_type):
        uploaded_filenames.append(filename)
        return "media-pdf"

    monkeypatch.setattr(main.requests, "head", fake_head)
    monkeypatch.setattr(main, "FILE_CATALOG", {"catalogo_pdf": item})
    monkeypatch.setattr(main, "upload_public_url_to_meta_media", fake_upload)
    monkeypatch.setattr(main, "post", lambda url, **kwargs: sent_payloads.append(kwargs["json"]) or Response(200))

    assert main.send_presaved_file("57300", "catalogo_pdf") is True
    assert uploaded_filenames[0].startswith("catalogo-tanaka-")
    assert uploaded_filenames[0].endswith(".pdf")
    assert sent_payloads[0]["document"]["filename"] == "Catálogo Tanaka.pdf"
    assert sent_payloads[0]["document"]["caption"] == "Aquí tienes nuestro catálogo completo ☺️"


def test_missing_catalog_does_not_send_broken_link(monkeypatch):
    calls = []
    item = types.SimpleNamespace(
        media_id=None,
        link="https://placeholder.test/catalog.pdf",
        filename="catalog.pdf",
        media_type="document",
        default_caption="Catálogo",
    )
    monkeypatch.setattr(main.requests, "head", lambda *args, **kwargs: Response(404))
    monkeypatch.setattr(main, "FILE_CATALOG", {"catalogo_pdf": item})
    monkeypatch.setattr(main, "post", lambda *args, **kwargs: calls.append((args, kwargs)))

    assert main.send_presaved_file("57300", "catalogo_pdf") is False
    assert calls == []
