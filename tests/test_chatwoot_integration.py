import hashlib
import hmac
import json
import os
import sys
import types
import requests

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


@pytest.fixture(autouse=True)
def synthetic_inbound_history(monkeypatch):
    """All provider tests use synthetic customer windows, never a live database."""
    monkeypatch.setattr(main, "get_message_logs", lambda *args, **kwargs: [
        {"role": "user", "content": "consulta", "metadata": {"received_at": main.datetime.now(main.timezone.utc).isoformat()}}
    ])
    monkeypatch.setattr(main, "update_follow_up_preferences", lambda *args: None)
    monkeypatch.setattr(main, "pause_bot_for_human_activity", lambda *args: None)


def _mock_followup_execution(monkeypatch):
    monkeypatch.setattr(main, "reserve_follow_up_attempt", lambda *a, **k: {"status": "reserved", "reservation_id": "lease"})
    monkeypatch.setattr(main, "begin_follow_up_send", lambda *a, **k: {"status": "sending"})
    monkeypatch.setattr(main, "finish_follow_up_attempt", lambda *a, **k: {"status": "sent"})
    original = (main.datetime.now(main.timezone.utc) - main.timedelta(hours=23)).isoformat()
    monkeypatch.setattr(main, "get_message_logs", lambda *a, **k: [{"role": "user", "content": "consulta",
        "metadata": {"received_at": original}}])
    monkeypatch.setattr(main, "next_eligible_contact_at", lambda now, **k: now)


def _mock_durable_handoff(monkeypatch):
    request = {"request_id": "request", "status": "pending", "attempts": 1}
    monkeypatch.setattr(main.handoff_delivery, "ensure_request", lambda *a: request)
    monkeypatch.setattr(main.handoff_delivery, "reserve_request", lambda *a: request)
    monkeypatch.setattr(main.handoff_delivery, "mark_creating", lambda *a: True)
    monkeypatch.setattr(main.handoff_delivery, "complete_request", lambda *a: True)


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
    _mock_durable_handoff(monkeypatch)
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda phone: {"chatwoot_conversation_id": None})
    monkeypatch.setattr(main, "update_chatwoot_conversation_id", lambda *args: None)
    monkeypatch.setattr(main, "get_message_logs", lambda *args, **kwargs: [{"role": "user", "content": "Necesito ayuda"}])
    monkeypatch.setattr(main, "save_message_log", lambda *args: None)
    monkeypatch.setattr(chatwoot_api, "get_or_create_contact", lambda *args, **kwargs: 12)
    monkeypatch.setattr(chatwoot_api, "create_conversation", lambda contact_id, **kwargs: 34)


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


def test_b2b_without_advisor_after_five_minutes_creates_priority_alert(monkeypatch):
    monkeypatch.setattr(main, "claim_internal_alert", lambda *_args: True)
    messages = []
    monkeypatch.setattr(main, "get_message_logs", lambda *args, **kwargs: [
        {"role": "system", "content": "HANDOFF: Transferido a humano. Razón: B2B_HIGH_VALUE: interés en maquila"}
    ])
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *_args: {
        "is_paused": True, "chatwoot_conversation_id": 34,
    })
    monkeypatch.setattr(chatwoot_api, "send_message_to_chatwoot", lambda *args, **kwargs: messages.append((args, kwargs)))

    main.escalate_unattended_b2b_handoff(
        "57300", 34, "B2B_HIGH_VALUE: interés en maquila"
    )

    assert len(messages) == 1
    assert "PRIORIDAD" in messages[0][0][1]
    assert messages[0][1]["is_private"] is True


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

    main._process_whatsapp_message_unlocked("57300", "Cliente", "Mándame el catálogo", customer_message_received_at=main.datetime.now(main.timezone.utc))

    assert state["is_paused"] is True
    assert "No se pudo entregar el catálogo" in state["handoff_reason"]
    assert any("Detalle técnico" in entry[2] and "meta_media_upload_failed" in entry[2] for entry in logs)
    assert customer_messages[-1] == (
        "Perdón, no pude adjuntar el catálogo. Se necesita revisión del equipo para enviártelo manualmente."
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


def test_bulk_catalog_recovery_requires_explicit_confirmation():
    with pytest.raises(ValueError):
        main.BulkCatalogRecoveryRequest(
            recoveries=[{"phone_number": "573001112233", "catalog_id": "catalogo_pdf"}],
            confirmation="SI",
        )
    request = main.BulkCatalogRecoveryRequest(
        recoveries=[{"phone_number": "573001112233", "catalog_id": "catalogo_pdf"}],
        confirmation="REENVIAR",
    )
    assert request.recoveries[0].catalog_id == "catalogo_pdf"


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
        customer_message_received_at=main.datetime.now(main.timezone.utc),
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
        main.send_whatsapp_message("15550000001", "hello")
    print(caught.value)
    output = capsys.readouterr().out
    assert "wa-secret-token" not in output and "Bearer leak" not in output


def test_meta_200_returns_success(monkeypatch):
    monkeypatch.setattr(main, "post", lambda *args, **kwargs: Response(200))
    assert main.send_whatsapp_message("15550000001", "hello") is True


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
        main._process_whatsapp_message_unlocked("57300", "Name", "hello", customer_message_received_at=main.datetime.now(main.timezone.utc))
    assert scheduled == []
    assert not any(entry[1] == "model" for entry in logged)


def test_followup_at_or_after_24_hours_is_cancelled(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(main, "queue_enabled", lambda: True)
    monkeypatch.setattr(main, "follow_up_delay_seconds", lambda minutes: 24 * 60 * 60)
    monkeypatch.setattr(main, "invalidate_follow_up", lambda phone: calls.append(("cancel", phone)))
    monkeypatch.setattr(main, "register_follow_up", lambda *a: pytest.fail("must not register"))
    monkeypatch.setattr(main, "enqueue_in", lambda *a, **k: pytest.fail("must not enqueue"))

    with main.message_trace(received_at=main.datetime.now(main.timezone.utc).isoformat()):
        main.schedule_follow_up("57300", "¿Sigues interesado?", 2880)

    assert calls == []  # An old job must never invalidate a newer sequence.
    assert "outside_whatsapp_window_no_template" in capsys.readouterr().out


def test_scheduled_followup_rechecks_meta_window_before_send(monkeypatch):
    sent = []
    cancelled = []
    monkeypatch.setattr(main, "claim_follow_up_attempt", lambda *args: True)
    monkeypatch.setattr(main, "meta_window_is_open", lambda *_args: False)
    monkeypatch.setattr(main, "invalidate_follow_up", lambda phone: cancelled.append(phone))
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *args: sent.append(args))

    main.send_scheduled_follow_up(
        "57300", "token", "mensaje", "CATALOG_SENT", 2,
        "2026-09-30T08:00:00-05:00", "2026-09-29T20:00:00+00:00",
    )

    assert sent == []
    assert cancelled == []


def test_scheduled_followup_rechecks_colombia_hours_before_send(monkeypatch):
    sent = []
    queued = []
    monkeypatch.setattr(main, "next_eligible_contact_at", lambda now, **kwargs: now + main.timedelta(hours=1))
    monkeypatch.setattr(main, "enqueue_in", lambda *args, **kwargs: queued.append(args))
    monkeypatch.setattr(main, "claim_follow_up_attempt", lambda *args: True)
    monkeypatch.setattr(main, "meta_window_is_open", lambda *_args: True)
    monkeypatch.setattr(main, "colombia_service_window_is_open", lambda: False)
    monkeypatch.setattr(main, "invalidate_follow_up", lambda *_args: None)
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *args: sent.append(args))

    main.send_scheduled_follow_up(
        "57300", "token", "mensaje", "FIRST_RESPONSE", 1,
        "", (main.datetime.now(main.timezone.utc) + main.timedelta(hours=3)).isoformat(),
    )

    assert sent == []
    assert len(queued) == 1


@pytest.mark.parametrize("stage", [None, "CATALOG_SENT"], ids=["legacy_callback", "catalog_sequence"])
@pytest.mark.parametrize("surrounding_whitespace", ["", " \n"])
def test_queued_followup_replaces_obsolete_catalog_text_and_logs_sent_text(
    monkeypatch, stage, surrounding_whitespace,
):
    from commercial_intents import DEFAULT_CATALOG_FOLLOW_UP_MESSAGE
    _mock_followup_execution(monkeypatch)

    obsolete_message = (
        "¿Viste alguna opción que te gustara? Dime si prefieres panadería o postres "
        "y te recomiendo dos opciones con precio 😊"
    )
    sent = []
    logged = []
    monkeypatch.setattr(main, "claim_follow_up_attempt", lambda *args: True)
    monkeypatch.setattr(main, "meta_window_is_open", lambda *_args: True)
    monkeypatch.setattr(main, "colombia_service_window_is_open", lambda: True)
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda _phone: {"is_paused": False})
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *args: sent.append(args))
    monkeypatch.setattr(main, "save_message_log", lambda *args: logged.append(args))
    monkeypatch.setattr(main, "operational_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "invalidate_follow_up", lambda *_args: pytest.fail("eligible reminder must stay active"))

    kwargs = {"stage": stage} if stage else {}
    main.send_scheduled_follow_up(
        "57300", "token", f"{surrounding_whitespace}{obsolete_message}{surrounding_whitespace}", **kwargs,
        meta_window_expires_at=(main.datetime.now(main.timezone.utc) + main.timedelta(hours=1)).isoformat(),
    )

    assert sent == [("57300", DEFAULT_CATALOG_FOLLOW_UP_MESSAGE)]
    assert logged == [("57300", "model", DEFAULT_CATALOG_FOLLOW_UP_MESSAGE)]


def test_queued_followup_preserves_custom_business_message(monkeypatch):
    _mock_followup_execution(monkeypatch)
    custom_message = "¿Cuántos kilos de queso necesitas para tu panadería?"
    sent = []
    logged = []
    monkeypatch.setattr(main, "claim_follow_up_attempt", lambda *args: True)
    monkeypatch.setattr(main, "meta_window_is_open", lambda *_args: True)
    monkeypatch.setattr(main, "colombia_service_window_is_open", lambda: True)
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda _phone: {"is_paused": False})
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *args: sent.append(args))
    monkeypatch.setattr(main, "save_message_log", lambda *args: logged.append(args))
    monkeypatch.setattr(main, "operational_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "invalidate_follow_up", lambda *_args: pytest.fail("eligible reminder must stay active"))

    main.send_scheduled_follow_up("57300", "token", custom_message, "CATALOG_SENT",
        meta_window_expires_at=(main.datetime.now(main.timezone.utc) + main.timedelta(hours=1)).isoformat())

    assert sent == [("57300", custom_message)]
    assert logged == [("57300", "model", custom_message)]


def test_sequence_meta_expiry_is_anchored_to_inbound_message(monkeypatch):
    queued = []
    inbound = main.datetime.now(main.timezone.utc) - main.timedelta(hours=1)
    plan = types.SimpleNamespace(
        stage="FIRST_RESPONSE", delays_minutes=(10,), messages=("mensaje",),
    )
    monkeypatch.setattr(main, "queue_enabled", lambda: True)
    monkeypatch.setattr(main, "follow_up_delay_seconds", lambda _minutes: 600)
    monkeypatch.setattr(main, "register_follow_up", lambda *_args, **_kwargs: "token")
    monkeypatch.setattr(main, "enqueue_in", lambda *args, **kwargs: queued.append((args, kwargs)))

    main.schedule_follow_up_sequence(
        "57300", plan, customer_message_received_at=inbound,
    )

    assert queued[0][0][8] == (inbound + main.timedelta(hours=24)).isoformat()


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


def test_agent_catalog_command_bypasses_chatwoot_attachment_upload(monkeypatch):
    event = payload()
    event["content"] = "/catalogo catalogo_portafolio"
    delivered = []
    logs = []
    monkeypatch.setattr(main, "get_phone_by_chatwoot_id", lambda _value: "57300")
    monkeypatch.setattr(fake_bot, "refresh_managed_catalogs", lambda: None, raising=False)
    monkeypatch.setattr(main, "deliver_presaved_file", lambda *args: delivered.append(args) or (True, None))
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *_args: pytest.fail("command text must stay internal"))
    monkeypatch.setattr(main, "save_message_log", lambda *args: logs.append(args))
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *args, **kwargs: None)
    monkeypatch.setattr(chatwoot_api, "update_message_status", lambda *args: None)

    main.process_chatwoot_event(event)

    assert delivered == [("57300", "catalogo_portafolio")]
    assert logs == [("57300", "asesor", "Catálogo enviado por asesor: catalogo_portafolio")]


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


def test_chatwoot_wav_is_converted_before_meta_upload(monkeypatch):
    uploaded = {}
    monkeypatch.setattr(
        main, "get",
        lambda *a, **k: Response(200, headers={"Content-Type": "audio/x-wav"}, chunks=[b"wav"]),
    )
    monkeypatch.setattr(
        chatwoot_api, "prepare_audio_for_delivery",
        lambda data, mime: (b"mp3", "audio/mpeg", ".mp3"),
    )

    def fake_post(url, **kwargs):
        uploaded.update(files=kwargs["files"], data=kwargs["data"])
        return Response(200, {"id": "media-audio"})

    monkeypatch.setattr(main, "post", fake_post)

    assert main.upload_chatwoot_attachment_to_meta("/voice.wav", filename="voice.wav") == "media-audio"
    assert uploaded["files"]["file"] == ("voice.mp3", b"mp3", "audio/mpeg")
    assert uploaded["data"] == {"messaging_product": "whatsapp"}


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
        ("57300", "Gracias por elegirnos. Cuando necesites algo más, escríbenos ☺️")
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

    assert main.send_presaved_file("15550000001", "catalogo_pdf") is True
    assert sent_payloads[0]["type"] == "image"
    assert sent_payloads[0]["image"]["id"] == "media-png"
    assert sent_payloads[0]["image"]["caption"] == "Catálogo Memo's"
    assert "filename" not in sent_payloads[0]["image"]


def test_pdf_catalog_uses_commercial_name_but_upload_keeps_cache_buster(monkeypatch):
    monkeypatch.setattr(main.config, "BUSINESS_ID", "tanaka")
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

    assert main.send_presaved_file("15550000001", "catalogo_pdf") is True
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

    assert main.send_presaved_file("15550000001", "catalogo_pdf") is False
    assert calls == []


def _whatsapp_payload(messages, contacts=None, phone_number_id="phone-id"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": phone_number_id},
            "contacts": contacts or [],
            "messages": messages,
        }}]}],
    }


def _capture_whatsapp_dispatch(monkeypatch):
    dispatched = []
    monkeypatch.setattr(main, "queue_enabled", lambda: False)
    monkeypatch.setattr(
        main, "_dispatch_before_ack",
        lambda _background, _function, *args, **kwargs: dispatched.append((args, kwargs)) or "queued",
    )
    return dispatched


@pytest.mark.parametrize("business_id", ["memos", "tanaka", "velvet", "future_shop"])
@pytest.mark.parametrize("identity_field", ["from_user_id", "from_parent_user_id", "from"])
def test_whatsapp_webhook_accepts_username_profile_identity_for_every_business(
    monkeypatch, business_id, identity_field,
):
    sender_id = "CO.ENT.TestSender01" if identity_field == "from_parent_user_id" else "CO.TestSender01"
    contact_field = "parent_user_id" if identity_field == "from_parent_user_id" else "user_id"
    monkeypatch.setattr(main.config, "BUSINESS_ID", business_id)
    dispatched = _capture_whatsapp_dispatch(monkeypatch)
    message = {"id": "wamid.test-inbound", "type": "text", "text": {"body": "Hola"}, identity_field: sender_id}
    contacts = [{contact_field: sender_id, "profile": {"name": "Cliente de prueba", "username": "test_customer"}}]

    response = TestClient(main.app).post("/webhook", json=_whatsapp_payload([message], contacts))

    assert response.status_code == 200
    assert response.json()["accepted"] == 1
    assert response.json()["rejected"] == 0
    assert dispatched[0][0][:3] == (sender_id, "Cliente de prueba", "Hola")
    assert dispatched[0][1] == {
        "event_id": "wamid.test-inbound", "business_id": business_id, "phone_number_id": "phone-id",
    }


def test_whatsapp_media_webhook_accepts_bsuid_without_phone_number(monkeypatch):
    dispatched = _capture_whatsapp_dispatch(monkeypatch)
    message = {
        "id": "wamid.test-audio", "from_user_id": "CO.TestSender01", "type": "audio",
        "audio": {"id": "media-test-audio", "mime_type": "audio/ogg; codecs=opus"},
    }

    response = TestClient(main.app).post("/webhook", json=_whatsapp_payload([message]))

    assert response.json()["accepted"] == 1
    assert dispatched[0][0] == (
        "CO.TestSender01", "Cliente", "[Audio Nota]", False, "media-test-audio", True,
        "media-test-audio", "audio", "audio/ogg; codecs=opus", None,
    )


def test_whatsapp_batch_matches_each_sender_with_its_own_contact(monkeypatch):
    dispatched = _capture_whatsapp_dispatch(monkeypatch)
    messages = [
        {"id": "wamid.first", "from_user_id": "CO.TestSender01", "type": "text", "text": {"body": "Primero"}},
        {"id": "wamid.second", "from_user_id": "CO.TestSender02", "type": "text", "text": {"body": "Segundo"}},
    ]
    contacts = [
        {"user_id": "CO.TestSender02", "profile": {"name": "Segundo cliente"}},
        {"user_id": "CO.TestSender01", "profile": {"name": "Primer cliente"}},
    ]

    response = TestClient(main.app).post("/webhook", json=_whatsapp_payload(messages, contacts))

    assert response.json()["accepted"] == 2
    assert [args[:3] for args, _kwargs in dispatched] == [
        ("CO.TestSender01", "Primer cliente", "Primero"),
        ("CO.TestSender02", "Segundo cliente", "Segundo"),
    ]


@pytest.mark.parametrize("include_message_phone", [False, True])
def test_whatsapp_username_transition_keeps_an_available_phone_history(monkeypatch, include_message_phone):
    dispatched = _capture_whatsapp_dispatch(monkeypatch)
    message = {
        "id": "wamid.phone-history", "from_user_id": "CO.TestSender01", "type": "text", "text": {"body": "Hola"},
    }
    if include_message_phone:
        message["from"] = "15550000001"
    contact = {"wa_id": "15550000001", "user_id": "CO.TestSender01", "profile": {"name": "Cliente existente"}}

    response = TestClient(main.app).post("/webhook", json=_whatsapp_payload([message], [contact]))

    assert response.json()["accepted"] == 1
    assert dispatched[0][0][:3] == ("15550000001", "Cliente existente", "Hola")


def test_whatsapp_username_identity_does_not_take_an_unrelated_contacts_phone_or_name(monkeypatch):
    dispatched = _capture_whatsapp_dispatch(monkeypatch)
    message = {"id": "wamid.unmatched-contact", "from_user_id": "CO.TestSender01", "type": "text", "text": {"body": "Hola"}}
    unrelated_contact = {
        "wa_id": "15550000002", "user_id": "CO.TestSender02", "profile": {"name": "Otro cliente"},
    }

    response = TestClient(main.app).post("/webhook", json=_whatsapp_payload([message], [unrelated_contact]))

    assert response.json()["accepted"] == 1
    assert dispatched[0][0][:3] == ("CO.TestSender01", "Cliente", "Hola")


@pytest.mark.parametrize("contact", [
    {"user_id": "CO.TestSender01", "profile": {"name": "Cliente de prueba"}},
    {"parent_user_id": "CO.ENT.TestSender01", "profile": {"name": "Cliente de prueba"}},
    {"wa_id": "15550000001", "profile": {"name": "Cliente de prueba"}},
])
def test_whatsapp_single_message_can_use_one_unambiguous_contact(monkeypatch, contact):
    dispatched = _capture_whatsapp_dispatch(monkeypatch)
    message = {"id": "wamid.contact-fallback", "type": "text", "text": {"body": "Hola"}}
    sender_id = next(value for key, value in contact.items() if key != "profile")

    response = TestClient(main.app).post("/webhook", json=_whatsapp_payload([message], [contact]))

    assert response.json()["accepted"] == 1
    assert dispatched[0][0][:3] == (sender_id, "Cliente de prueba", "Hola")


def test_whatsapp_batch_never_assigns_the_same_contact_to_messages_without_sender(monkeypatch):
    dispatched = _capture_whatsapp_dispatch(monkeypatch)
    messages = [
        {"id": "wamid.unknown-one", "type": "text", "text": {"body": "Uno"}},
        {"id": "wamid.unknown-two", "type": "text", "text": {"body": "Dos"}},
    ]
    contacts = [{"user_id": "CO.TestSender01", "profile": {"name": "Cliente de prueba"}}]

    response = TestClient(main.app).post("/webhook", json=_whatsapp_payload(messages, contacts))

    assert response.json()["accepted"] == 0
    assert response.json()["rejected"] == 2
    assert dispatched == []


@pytest.mark.parametrize("sender_fields,contacts", [
    ({}, [{"profile": {"username": "test_customer", "name": "Cliente"}}]),
    ({"from": "@test_customer"}, []),
    ({"from": "wamid.15550000001"}, []),
    ({"from_user_id": "test_customer"}, []),
    ({"from": "１５５５００００００１"}, []),
])
def test_whatsapp_webhook_does_not_invent_phone_from_message_id_or_username(
    monkeypatch, sender_fields, contacts,
):
    dispatched = _capture_whatsapp_dispatch(monkeypatch)
    message = {"id": "wamid.15550000001", "type": "text", "text": {"body": "Hola"}, **sender_fields}

    response = TestClient(main.app).post("/webhook", json=_whatsapp_payload([message], contacts))

    assert response.json()["accepted"] == 0
    assert response.json()["rejected"] == 1
    assert dispatched == []


@pytest.mark.parametrize("destination", ["other-business-number", None])
def test_whatsapp_bsuid_does_not_bypass_destination_isolation(monkeypatch, destination):
    dispatched = _capture_whatsapp_dispatch(monkeypatch)
    message = {"id": "wamid.wrong-destination", "from_user_id": "CO.TestSender01", "type": "text", "text": {"body": "Hola"}}

    response = TestClient(main.app).post("/webhook", json=_whatsapp_payload([message], phone_number_id=destination))

    assert response.json()["accepted"] == 0
    assert response.json()["rejected"] == 1
    assert dispatched == []


def test_queued_whatsapp_worker_accepts_bsuid_for_its_own_business_only(monkeypatch):
    claimed = []
    processed = []
    monkeypatch.setattr(main.config, "BUSINESS_ID", "future_shop")
    monkeypatch.setattr(main, "claim_webhook_event", lambda *args: claimed.append(args) or True)
    monkeypatch.setattr(main, "process_whatsapp_message", lambda *args, **kwargs: processed.append((args, kwargs)))

    main.process_claimed_whatsapp_event(
        "CO.TestSender01", "Cliente de prueba", "Hola", event_id="wamid.queued",
        business_id="future_shop", phone_number_id="phone-id",
    )

    assert claimed == [("whatsapp", "wamid.queued", "CO.TestSender01")]
    assert processed == [(("CO.TestSender01", "Cliente de prueba", "Hola"), {"event_id": "wamid.queued"})]


@pytest.mark.parametrize("business_id,phone_number_id", [
    ("another_shop", "phone-id"), ("future_shop", "other-business-number"),
])
def test_queued_whatsapp_bsuid_cannot_cross_deployment_boundaries(monkeypatch, business_id, phone_number_id):
    monkeypatch.setattr(main.config, "BUSINESS_ID", "future_shop")
    monkeypatch.setattr(main, "claim_webhook_event", lambda *args: pytest.fail("must reject before claiming"))
    monkeypatch.setattr(main, "process_whatsapp_message", lambda *args, **kwargs: pytest.fail("must not process"))

    main.process_claimed_whatsapp_event(
        "CO.TestSender01", "Cliente de prueba", "Hola", event_id="wamid.wrong-worker",
        business_id=business_id, phone_number_id=phone_number_id,
    )


@pytest.mark.parametrize("sender_id", ["15550000001", "CO.TestSender01", "CO.ENT.TestSender01"])
@pytest.mark.parametrize("message_kind", ["text", "media", "catalog"])
def test_all_whatsapp_delivery_paths_use_the_correct_recipient_field(monkeypatch, sender_id, message_kind):
    sent = []
    monkeypatch.setattr(main, "post", lambda url, **kwargs: sent.append(kwargs["json"]) or Response(200))
    if message_kind == "text":
        assert main.send_whatsapp_message(sender_id, "Hola") is True
    elif message_kind == "media":
        assert main.send_whatsapp_media(sender_id, "media-test", "image", "Imagen de prueba") is True
    else:
        item = types.SimpleNamespace(
            media_id="media-catalog", link=None, filename="Catálogo de prueba.pdf",
            media_type="document", default_caption="Opciones disponibles",
        )
        monkeypatch.setattr(main, "FILE_CATALOG", {"catalogo_test": item})
        assert main.send_presaved_file(sender_id, "catalogo_test") is True

    assert len(sent) == 1
    assert sent[0]["messaging_product"] == "whatsapp"
    if sender_id.isascii() and sender_id.isdigit():
        assert sent[0]["to"] == sender_id
        assert "recipient" not in sent[0]
    else:
        assert sent[0]["recipient"] == sender_id
        assert sent[0]["recipient_type"] == "individual"
        assert "to" not in sent[0]


@pytest.mark.parametrize("sender_id", ["CO.TestSender01", "CO.ENT.TestSender01"])
def test_manual_handoff_and_catalog_recovery_accept_bsuid(sender_id):
    assert main.ManualHandoffRequest(phone_number=sender_id).phone_number == sender_id
    assert main.CatalogRecoverySelection(phone_number=sender_id, catalog_id="catalogo_test").phone_number == sender_id


def test_bsuid_reset_restarts_the_existing_identity_without_creating_phone_state(monkeypatch):
    reset = []
    cancelled = []
    sent = []
    monkeypatch.setattr(main, "invalidate_follow_up", lambda sender_id: cancelled.append(sender_id))
    monkeypatch.setattr(main, "reset_client_history", lambda sender_id: reset.append(sender_id))
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *args: sent.append(args) or True)
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *args: pytest.fail("reset must not create another identity"))

    main._process_whatsapp_message_unlocked("CO.TestSender01", "Cliente de prueba", "/reset")

    assert reset == ["CO.TestSender01"]
    assert cancelled == ["CO.TestSender01"]
    assert sent == [("CO.TestSender01", "🔄 Conversación reiniciada. El bot está activo y empezamos de cero.")]


def test_bsuid_handoff_display_name_does_not_present_the_identity_as_a_phone(monkeypatch):
    _mock_handoff_dependencies(monkeypatch)
    contacts = []
    monkeypatch.setattr(main, "invalidate_follow_up", lambda *_args: None)
    monkeypatch.setattr(chatwoot_api, "get_or_create_contact", lambda *args, **kwargs: contacts.append((args, kwargs)) or 12)
    monkeypatch.setattr(chatwoot_api, "send_message_to_chatwoot", lambda *args, **kwargs: None)

    main._create_handoff_ticket_if_needed(
        "CO.TestSender01", "Cliente de prueba", {"is_paused": True, "handoff_reason": "Necesita asesor"},
        None, "", None, None,
    )

    assert contacts[0][0] == ("CO.TestSender01",)
    assert "+CO.TestSender01" not in contacts[0][1]["name"]
    assert "Cliente de prueba" in contacts[0][1]["name"]


@pytest.fixture
def guarded_followups(monkeypatch):
    import fakeredis
    import queue_client
    import follow_up_store
    from datetime import datetime, timezone

    now = datetime(2026, 10, 21, 15, tzinfo=timezone.utc)
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    now = Frozen(2026, 10, 21, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(main, "datetime", Frozen)
    monkeypatch.setattr(follow_up_store, "datetime", Frozen)
    monkeypatch.setattr(main.config, "BUSINESS_ID", "memos")
    monkeypatch.setattr(main.config, "REDIS_URL", None)
    monkeypatch.setenv("BUSINESS_ID", "memos")
    monkeypatch.setenv("WA_PHONE_NUMBER_ID", "phone-id")
    connection = fakeredis.FakeRedis()
    monkeypatch.setattr(queue_client, "get_queue", lambda: types.SimpleNamespace(connection=connection))
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *a: {"is_paused": False})
    original = now - main.timedelta(hours=21)
    monkeypatch.setattr(main, "get_message_logs", lambda *a, **k: [{"role": "user", "content": "consulta",
        "metadata": {"received_at": original.isoformat()}}])
    monkeypatch.setattr(main, "save_message_log", lambda *a, **k: None)
    queued, sent = [], []
    monkeypatch.setattr(main, "enqueue_in", lambda *a, **k: queued.append((a, k)))
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *a: sent.append(a) or True)
    token = queue_client.register_follow_up("57300", 86400, reference_at=now)
    expiry = (now + main.timedelta(hours=3)).isoformat()
    return types.SimpleNamespace(now=now, token=token, expiry=expiry, queued=queued, sent=sent, redis=connection)


def test_accumulated_jobs_respect_execution_spacing(guarded_followups):
    f = guarded_followups
    main.send_scheduled_follow_up("57300", f.token, "mensaje", "CATALOG_SENT", 1, "", f.expiry)
    main.send_scheduled_follow_up("57300", f.token, "mensaje", "CATALOG_SENT", 2, "", f.expiry)
    assert len(f.sent) == 1
    assert len(f.queued) == 1
    assert f.queued[0][0][0] >= 900
    assert f.queued[0][0][8] == f.expiry


def test_old_expired_job_does_not_cancel_new_sequence(guarded_followups):
    import queue_client
    from follow_up_store import keys
    f = guarded_followups
    newer = queue_client.register_follow_up("57300", 86400, reference_at=f.now)
    main.send_scheduled_follow_up("57300", f.token, "mensaje", "CATALOG_SENT", 1, "",
        (f.now - main.timedelta(hours=1)).isoformat())
    assert f.redis.get(keys("57300")[0]).decode() == newer
    assert f.sent == []


def test_legacy_expiry_without_original_timestamp_cannot_extend_window(guarded_followups, monkeypatch):
    f = guarded_followups
    monkeypatch.setattr(main, "get_message_logs", lambda *a, **k: [])
    result = main.send_scheduled_follow_up("57300", f.token, "mensaje", "CATALOG_SENT", 1, "", f.expiry)
    assert result["status"] == "legacy_missing_verified_original_timestamp"
    assert f.sent == []


def test_reprogramming_cannot_promote_an_unverified_legacy_reference(guarded_followups):
    f = guarded_followups
    future = (f.now + main.timedelta(minutes=15)).isoformat()
    main.send_scheduled_follow_up("57300", f.token, "mensaje", "CATALOG_SENT", 1, future, f.expiry)
    assert len(f.queued) == 1
    assert f.queued[0][1]["reference_at"] is None
    assert f.sent == []


@pytest.mark.parametrize("error,expected,queued", [
    (ProviderError("meta", "send", 429), "confirmed_provider_rejection_retry", 1),
    (ProviderError("meta", "send", None, confirmed_not_sent=True), "confirmed_pre_send_failure_retry", 1),
    (ProviderError("meta", "send", None), "provider_result_ambiguous_manual_review", 0),
    (ProviderError("meta", "send", 503), "provider_result_ambiguous_manual_review", 0),
])
def test_provider_rejection_and_ambiguity_have_different_recovery(guarded_followups, monkeypatch, error, expected, queued):
    f = guarded_followups
    attempts = []
    def send(*args):
        attempts.append(args)
        raise error
    monkeypatch.setattr(main, "send_whatsapp_message", send)
    result = main.send_scheduled_follow_up("57300", f.token, "mensaje", "CATALOG_SENT", 1, "", f.expiry)
    assert result["status"] == expected
    assert len(f.queued) == queued
    if not queued:
        main.send_scheduled_follow_up("57300", f.token, "mensaje", "CATALOG_SENT", 1, "", f.expiry)
        assert len(attempts) == 1


def test_human_control_and_empty_legacy_jobs_cannot_send(guarded_followups, monkeypatch):
    f = guarded_followups
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *args: {"is_paused": True})
    result = main.send_scheduled_follow_up("57300", f.token, "mensaje", "CATALOG_SENT", 1, "", f.expiry)
    assert result["status"] == "human_control"
    result = main.send_scheduled_follow_up("57300", f.token, "mensaje")
    assert result["status"] == "legacy_missing_original_window"
    assert f.sent == []


def test_inbound_timestamp_reaches_queue_unchanged(monkeypatch):
    client = TestClient(main.app)
    dispatched = []
    observed = []
    monkeypatch.setattr(main, "_dispatch_before_ack", lambda _bg, _fn, *a, **k: dispatched.append(k))
    monkeypatch.setattr(main, "record_customer_activity", lambda *a: observed.append(a))
    value = {"metadata": {"phone_number_id": "phone-id"}, "contacts": [], "messages": [
        {"from": "573174479494", "id": "wamid.original", "timestamp": "1792591200", "type": "text", "text": {"body": "hola"}}
    ]}
    response = client.post("/webhook", json={"object": "whatsapp_business_account", "entry": [{"changes": [{"value": value}]}]})
    assert response.status_code == 200
    original = main.datetime.fromtimestamp(1792591200, main.timezone.utc)
    assert dispatched[0]["customer_message_received_at"] == original.isoformat()
    assert observed[0][1] == original


def test_expired_inbound_is_logged_and_escalated_without_freeform_send(guarded_followups, monkeypatch):
    f = guarded_followups
    handoffs, logs = [], []
    monkeypatch.setattr(main, "process_message_logic", lambda *a: pytest.fail("must not infer a freeform reply outside the original window"))
    monkeypatch.setattr(main, "pause_bot_for_handoff", lambda *a: None)
    monkeypatch.setattr(main, "_create_handoff_ticket_if_needed", lambda *a: handoffs.append(a))
    monkeypatch.setattr(main, "save_message_log", lambda *a: logs.append(a))
    main._process_whatsapp_message_unlocked("57300", "Cliente", "consulta pendiente",
        customer_message_received_at=f.now - main.timedelta(hours=25))
    assert f.sent == []
    assert handoffs
    assert any(row[1] == "user" for row in logs)


def test_handoff_failure_keeps_paused_and_cannot_promise_created(monkeypatch):
    state = {"is_paused": True, "handoff_reason": "B2B_HIGH_VALUE: precios al por mayor"}
    turn = types.SimpleNamespace(response="Ya te transferí con un asesor", requested_files=[],
        send_files_before_response=False, handoff_requested=True, follow_up_message="seguir", follow_up_delay_minutes=10)
    sequence, sent = [], []
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *a: dict(state))
    # Start active, then model transitions to paused.
    calls = []
    def get_state(*args):
        calls.append(args)
        return {"is_paused": len(calls) > 1, "handoff_reason": state["handoff_reason"]}
    monkeypatch.setattr(main, "get_or_create_customer_state", get_state)
    monkeypatch.setattr(main, "process_message_logic", lambda *a: turn)
    monkeypatch.setattr(main, "_create_handoff_ticket_if_needed", lambda *a: sequence.append("create_pending") or {"status": "pending"})
    monkeypatch.setattr(main, "send_whatsapp_message", lambda _p, text: sequence.append("send") or sent.append(text))
    monkeypatch.setattr(main, "save_message_log", lambda *a: None)
    monkeypatch.setattr(main, "schedule_follow_up_sequence", lambda *a, **k: pytest.fail("human control must cancel followups"))
    main._process_whatsapp_message_unlocked("57300", "Cliente", "Eso es precios al por mayor",
        customer_message_received_at=main.datetime.now(main.timezone.utc))
    assert sequence[:2] == ["create_pending", "send"]
    assert "pendiente" in sent[0]
    assert "Ya te transferí" not in sent[0]


@pytest.mark.parametrize("error,ambiguous", [(ProviderError("chatwoot", "create_conversation", 429), False),
    (requests.exceptions.Timeout("ambiguous timeout"), True)])
def test_handoff_provider_failure_never_reenables_bot(monkeypatch, error, ambiguous):
    _mock_handoff_dependencies(monkeypatch)
    failed = []
    monkeypatch.setattr(chatwoot_api, "create_conversation", lambda *a, **k: (_ for _ in ()).throw(error))
    monkeypatch.setattr(main.handoff_delivery, "fail_request", lambda *a, **k: failed.append(k) or {"status": "unknown" if ambiguous else "pending"})
    monkeypatch.setattr(main, "_schedule_handoff_retry", lambda *a: None)
    monkeypatch.setattr(main, "recover_failed_handoff", lambda *a: pytest.fail("must keep human control"))
    result = main._create_handoff_ticket_if_needed("57300", "Cliente", {"is_paused": True}, None, "pedido", None, None)
    assert result["status"] == ("unknown" if ambiguous else "pending")
    assert failed[0]["ambiguous"] is ambiguous


def test_connect_timeout_is_confirmed_pre_send_but_read_timeout_is_ambiguous(monkeypatch):
    for error, confirmed in [(requests.exceptions.ConnectTimeout(), True), (requests.exceptions.ReadTimeout(), False)]:
        monkeypatch.setattr(main, "post", lambda *a, **k: (_ for _ in ()).throw(error))
        with pytest.raises(ProviderError) as caught:
            main.send_whatsapp_message("573001234567", "mensaje sintético")
        assert caught.value.confirmed_not_sent is confirmed
