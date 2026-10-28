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


@pytest.mark.parametrize("body_data,reason", [
    ({"event": "conversation_status_changed", "id": 44, "inbox_id": 20, "status": "resolved"}, "scope_missing"),
    ({"event": "conversation_updated", "account": {"id": 10}, "id": 44, "inbox_id": 21, "status": "open"}, "scope_mismatch"),
])
def test_scope_rejection_is_diagnosable_without_logging_customer_data_or_accepting_event(
        monkeypatch, capsys, body_data, reason):
    client = TestClient(main.app)
    monkeypatch.setattr(main.time, "time", lambda: 2000000000)
    monkeypatch.setattr(main, "claim_webhook_event", lambda *args: pytest.fail("rejected event cannot be claimed"))
    monkeypatch.setattr(main, "_dispatch_before_ack", lambda *args, **kwargs: pytest.fail("rejected event cannot be queued"))
    body_data.update(content="PRIVATE_CUSTOMER_CONTENT", api_access_token="PRIVATE_TOKEN",
                     contact={"phone_number": "PRIVATE_PHONE"})
    body = json.dumps(body_data).encode()
    assert client.post("/chatwoot-webhook", content=body, headers=signed(body)).status_code == 403
    events = [json.loads(line.split(" ", 1)[1]) for line in capsys.readouterr().out.splitlines()
              if line.startswith("[OPERATIONAL_EVENT]")]
    rejection = next(event for event in events if event["event"] == "CHATWOOT_WEBHOOK_REJECTED")
    assert rejection["reason"] == reason
    assert rejection["conversation_id"] == "44"
    assert rejection["chatwoot_event"] == body_data["event"]
    assert rejection["conversation_status"] == body_data["status"]
    assert rejection["expected_account_id"] == "10"
    assert rejection["expected_inbox_id"] == "20"
    assert "PRIVATE_" not in json.dumps(events)


@pytest.mark.parametrize("value", [[], "invalid_event", None, 123])
def test_signed_nonobject_chatwoot_webhook_returns_400_without_processing(monkeypatch, value):
    client = TestClient(main.app)
    monkeypatch.setattr(main.time, "time", lambda: 2000000000)
    monkeypatch.setattr(main, "_dispatch_before_ack", lambda *args, **kwargs: pytest.fail("invalid event cannot be queued"))
    body = json.dumps(value).encode()
    assert client.post("/chatwoot-webhook", content=body, headers=signed(body)).status_code == 400


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


def test_audio_handoff_identifies_trigger_transcription_without_unverified_audio(monkeypatch):
    _mock_handoff_dependencies(monkeypatch)
    monkeypatch.setattr(main, "save_message_log", lambda *args, **kwargs: None)
    notes = []
    monkeypatch.setattr(
        chatwoot_api, "download_meta_media",
        lambda media_id: pytest.fail("handoff downloaded audio a second time"),
    )
    monkeypatch.setattr(chatwoot_api, "send_message_to_chatwoot", lambda *args, **kwargs: notes.append((args, kwargs)))
    monkeypatch.setattr(
        chatwoot_api, "send_audio_to_chatwoot",
        lambda *args, **kwargs: pytest.fail("unverified voice note must not be attached"),
    )
    monkeypatch.setattr(
        chatwoot_api, "send_media_to_chatwoot",
        lambda *args, **kwargs: pytest.fail("unverified audio must not be uploaded"),
    )

    main._create_handoff_ticket_if_needed(
        "57300", "Cliente", {"is_paused": True, "handoff_reason": "Escuchar audio"},
        "audio-1", "transcripción", "audio/ogg; codecs=opus", None,
        effective_media_type="audio", media_bytes=b"original-ogg",
        downloaded_mime_type="audio/ogg; codecs=opus",
        audio_transcript="Quiero una nota escrita a mano para mañana",
    )

    assert len(notes) == 2
    summary = notes[0][0][1]
    assert "Quiero una nota escrita a mano para mañana" in summary
    assert "transcripci" in summary.lower()
    assert "Origen de la derivación" in summary
    assert all(kwargs["is_private"] is True for _, kwargs in notes)


def test_audio_transcription_reaches_handoff_summary_as_trigger_without_a_second_incoming_row(monkeypatch):
    _mock_durable_handoff(monkeypatch)
    monkeypatch.setattr(main.config, "REDIS_URL", None)
    state = {"is_paused": False, "chatwoot_conversation_id": None}
    history, notes, replies, updates = [], [], [], []
    transcript = "Quiero incluir una nota escrita a mano con el regalo de mañana"
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *args: state)
    monkeypatch.setattr(main, "get_message_logs", lambda *args, **kwargs: history[:])

    def log(phone, role, content, **kwargs):
        history.append({"role": role, "content": content, "metadata": {**main.current_trace(), **kwargs.get("metadata", {})}})

    def update(phone, event_id, text):
        updates.append((phone, event_id, text))
        row = next(item for item in history if item["role"] == "user")
        row.update(content=text)
        row["metadata"].update(input_type="audio", is_transcription=True)

    def logic(*args):
        assert args[1] == transcript
        state.update(is_paused=True, current_state="HUMAN_HANDOFF", handoff_reason="Validar la nota personalizada")
        return types.SimpleNamespace(response="Tu solicitud necesita revisión del equipo", requested_files=[],
            send_files_before_response=False, follow_up_message="")

    monkeypatch.setattr(main, "save_message_log", log)
    monkeypatch.setattr(main, "update_inbound_transcript", update)
    monkeypatch.setattr(main, "process_message_logic", logic)
    monkeypatch.setattr(main, "transcribe_audio_message", lambda *args: transcript)
    monkeypatch.setattr(main, "update_chatwoot_conversation_id", lambda phone, conv: state.update(chatwoot_conversation_id=conv))
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *args: replies.append(args))
    monkeypatch.setattr(chatwoot_api, "download_meta_media", lambda *args: (b"synthetic-voice", "audio/ogg"))
    monkeypatch.setattr(chatwoot_api, "get_or_create_contact", lambda *args, **kwargs: 12)
    monkeypatch.setattr(chatwoot_api, "create_conversation", lambda *args, **kwargs: 34)
    monkeypatch.setattr(chatwoot_api, "send_message_to_chatwoot", lambda *args, **kwargs: notes.append((args, kwargs)) or Response(200))
    monkeypatch.setattr(chatwoot_api, "send_audio_to_chatwoot", lambda *args, **kwargs: pytest.fail("audio must not be attached"))
    monkeypatch.setattr(chatwoot_api, "send_media_to_chatwoot", lambda *args, **kwargs: pytest.fail("audio must not be uploaded"))
    main.process_whatsapp_message("573001234567", "Cliente sintético", "[Audio Nota]",
        is_audio=True, media_id="synthetic-voice", media_type="audio", mime_type="audio/ogg",
        event_id="synthetic-voice-trigger", customer_message_received_at=main.datetime.now(main.timezone.utc))
    assert len([row for row in history if row["role"] == "user"]) == 1
    assert updates == [("573001234567", "synthetic-voice-trigger", transcript)]
    assert state["chatwoot_conversation_id"] == 34
    assert len(replies) == 1
    summary = notes[0][0][1]
    assert "**Origen de la derivación:**" in summary
    assert "**Transcripción automática que motivó la derivación:**" in summary
    assert "🎙️ [Transcripción automática de nota de voz]" in summary
    assert transcript in summary
    assert all(kwargs["is_private"] for _, kwargs in notes)


def test_audio_conversion_failure_sends_transcript_without_attaching_original(monkeypatch):
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
        34, b"original-ogg", "audio/ogg; codecs=opus", content="voice", is_private=False,
        transcript="Necesito confirmar mi pedido",
    )

    assert uploads == []
    assert "Necesito confirmar mi pedido" in notices[0][0][1]
    assert "transcripci" in notices[0][0][1].lower()
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


def test_paused_conversation_routes_labeled_audio_transcript_without_attachment(monkeypatch):
    sent = []
    monkeypatch.setattr(main, "invalidate_follow_up", lambda *args: None)
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *args: {"is_paused": True, "chatwoot_conversation_id": 44})
    monkeypatch.setattr(main, "save_message_log", lambda *args: None)
    monkeypatch.setattr(chatwoot_api, "download_meta_media", lambda media_id: (b"ogg", "audio/ogg; codecs=opus"))
    monkeypatch.setattr(chatwoot_api, "send_media_to_chatwoot", lambda *args, **kwargs: pytest.fail("generic uploader used"))
    monkeypatch.setattr(chatwoot_api, "send_audio_to_chatwoot", lambda *args, **kwargs: pytest.fail("unverified audio attached"))
    monkeypatch.setattr(main, "transcribe_audio_message", lambda *args: "¿Puedes confirmar la entrega?")
    monkeypatch.setattr(main, "update_inbound_transcript", lambda *args: None)
    monkeypatch.setattr(chatwoot_api, "send_message_to_chatwoot", lambda *args, **kwargs: sent.append((args, kwargs)) or Response(200))

    main._process_whatsapp_message_unlocked(
        "57300", "Cliente", "", media_id="audio-1", is_audio=True,
        audio_media_id="audio-1", media_type="audio", mime_type="audio/ogg; codecs=opus",
        customer_message_received_at=main.datetime.now(main.timezone.utc),
    )

    assert sent[0][0][0] == 44
    assert "Transcripción automática de nota de voz" in sent[0][0][1]
    assert "¿Puedes confirmar la entrega?" in sent[0][0][1]
    assert sent[0][1]["is_private"] is False


@pytest.fixture
def human_media_delivery(monkeypatch):
    state = {"is_paused": True, "chatwoot_conversation_id": 44, "current_state": "HUMAN_HANDOFF"}
    seen = {"uploads": [], "notes": [], "logs": [], "marked": [], "transcripts": []}
    monkeypatch.setattr(main.config, "REDIS_URL", None)
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *args: state)
    monkeypatch.setattr(main, "process_message_logic", lambda *args: pytest.fail("human control must remain active"))
    monkeypatch.setattr(main, "pause_bot_for_handoff", lambda *args: pytest.fail("do not create another emergency handoff"))
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *args: pytest.fail("do not send a customer fallback during human attention"))
    monkeypatch.setattr(main, "schedule_follow_up_sequence", lambda *args, **kwargs: pytest.fail("human attention cannot schedule followups"))
    monkeypatch.setattr(main, "save_message_log", lambda *args, **kwargs: seen["logs"].append((args, main.current_trace())))
    monkeypatch.setattr(main, "update_inbound_transcript", lambda *args: seen["transcripts"].append(args))
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *args, **kwargs: seen["marked"].append((args, kwargs)))
    monkeypatch.setattr(chatwoot_api, "send_audio_to_chatwoot", lambda *args, **kwargs: pytest.fail("do not attach unverified audio"))
    monkeypatch.setattr(chatwoot_api, "send_message_to_chatwoot", lambda *args, **kwargs: seen["notes"].append((args, kwargs)) or Response(200))
    monkeypatch.setattr(chatwoot_api, "download_meta_media", lambda *args: (b"synthetic-image", "image/jpeg"))
    return state, seen


@pytest.mark.parametrize("business_id", ["memos", "tanaka", "velvet", "future_brand"])
@pytest.mark.parametrize("verified", [False, True])
def test_human_image_delivery_requires_verified_attachment_and_never_blindly_resends(
        monkeypatch, human_media_delivery, business_id, verified):
    state, seen = human_media_delivery
    monkeypatch.setattr(main.config, "BUSINESS_ID", business_id)
    monkeypatch.setattr(chatwoot_api, "send_media_to_chatwoot",
        lambda *args, **kwargs: seen["uploads"].append((args, kwargs)) or (Response(200) if verified else None))
    main.process_whatsapp_message("573001234567", "Cliente sintético", "",
        is_image=True, media_id="synthetic-image", media_type="image", mime_type="image/jpeg",
        event_id="synthetic-image-event", customer_message_received_at=main.datetime.now(main.timezone.utc))
    assert len(seen["uploads"]) == 1
    assert seen["uploads"][0][1]["is_private"] is False
    assert state["is_paused"] and state["chatwoot_conversation_id"] == 44
    incoming = [row for row in seen["logs"] if row[0][1] == "user"]
    assert len(incoming) == 1
    assert incoming[0][1]["input_type"] == "image"
    assert incoming[0][1]["source_event_id"] == "synthetic-image-event"
    if verified:
        assert seen["notes"] == []
        assert seen["marked"][-1][1] == {}
    else:
        assert len(seen["notes"]) == 1
        assert seen["notes"][0][1]["is_private"] is True
        assert "pendiente de revisión" in seen["notes"][0][0][1]
        assert seen["marked"][-1][1] == {"status": "failed", "error": "attachment_not_verified"}


def test_human_image_download_failure_preserves_caption_and_reports_missing_attachment(monkeypatch, human_media_delivery):
    state, seen = human_media_delivery
    monkeypatch.setattr(chatwoot_api, "download_meta_media", lambda *args: (None, None))
    monkeypatch.setattr(chatwoot_api, "send_media_to_chatwoot", lambda *args, **kwargs: pytest.fail("no bytes to upload"))
    main.process_whatsapp_message("573001234567", "Cliente sintético", "Este es el diseño",
        is_image=True, media_id="synthetic-image", media_type="image", mime_type="image/jpeg",
        event_id="synthetic-image-event", customer_message_received_at=main.datetime.now(main.timezone.utc))
    assert seen["notes"][0] == ((44, "Este es el diseño"), {"is_private": False})
    assert seen["notes"][1][1]["is_private"] is True
    assert seen["marked"][-1][1] == {"status": "failed", "error": "download_failed"}
    assert state["chatwoot_conversation_id"] == 44


@pytest.mark.parametrize("transcript", ["Quiero confirmar el diseño", None])
def test_audio_during_human_attention_is_transcript_or_explicit_manual_review(
        monkeypatch, human_media_delivery, transcript):
    state, seen = human_media_delivery
    monkeypatch.setattr(chatwoot_api, "download_meta_media", lambda *args: (b"synthetic-voice", "audio/ogg"))
    monkeypatch.setattr(chatwoot_api, "send_media_to_chatwoot", lambda *args, **kwargs: pytest.fail("audio cannot become an unverified attachment"))
    monkeypatch.setattr(main, "transcribe_audio_message", lambda *args: transcript)
    main.process_whatsapp_message("573001234567", "Cliente sintético", "[Audio Nota]",
        is_audio=True, media_id="synthetic-voice", media_type="audio", mime_type="audio/ogg",
        event_id="synthetic-voice-event", customer_message_received_at=main.datetime.now(main.timezone.utc))
    assert len(seen["notes"]) == 1
    if transcript:
        assert transcript in seen["notes"][0][0][1]
        assert "Transcripción automática" in seen["notes"][0][0][1]
        assert seen["notes"][0][1]["is_private"] is False
        assert seen["transcripts"] == [("573001234567", "synthetic-voice-event", transcript)]
    else:
        assert "no fue posible transcribirla" in seen["notes"][0][0][1]
        assert seen["notes"][0][1]["is_private"] is True
        assert seen["transcripts"] == []
    assert state["is_paused"] and state["chatwoot_conversation_id"] == 44


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
    assert statuses == [(44, 91, "sent")]
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
    monkeypatch.setattr(main, "upload_chatwoot_attachment_to_meta", lambda *a, **k: "media-1")
    monkeypatch.setattr(main, "send_whatsapp_media", lambda *a: sent.append(a) or True)
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *a, **k: None)
    monkeypatch.setattr(chatwoot_api, "update_message_status", lambda *a: None)
    main.process_chatwoot_event(event)
    assert sent == [("57300", "media-1", "image", "agent reply", "proof.png")]


@pytest.fixture
def outgoing_attachment_batch(monkeypatch):
    event = payload()
    event["content"] = None
    event["created_at"] = main.datetime.now(main.timezone.utc).isoformat()
    event["attachments"] = [
        {"data_url": f"/rails/photo-{index}.png", "content_type": "image/png",
         "file_type": "image", "file_name": f"photo-{index}.png"}
        for index in range(1, 10)
    ]
    observed = {"operations": [], "accepted": [], "statuses": [], "marked": [], "notes": [], "logs": []}
    monkeypatch.setattr(main, "get_phone_by_chatwoot_id", lambda _value: "57300")
    monkeypatch.setattr(main, "invalidate_follow_up", lambda _phone: None)
    monkeypatch.setattr(main, "save_message_log", lambda *args: observed["logs"].append(args))
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *args: pytest.fail("an attachment-only batch cannot send text"))
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *args, **kwargs: observed["marked"].append((args, kwargs)))
    monkeypatch.setattr(chatwoot_api, "update_message_status", lambda *args: observed["statuses"].append(args))
    monkeypatch.setattr(chatwoot_api, "send_message_to_chatwoot", lambda *args, **kwargs: observed["notes"].append((args, kwargs)))
    return event, observed


@pytest.mark.parametrize("business_id", ["tanaka", "velvet", "memos", "new_business"])
def test_public_outgoing_batch_forwards_all_nine_images_in_order_once(
        monkeypatch, outgoing_attachment_batch, business_id):
    event, observed = outgoing_attachment_batch
    monkeypatch.setattr(main.config, "BUSINESS_ID", business_id)

    def upload(url, mime, filename, **kwargs):
        observed["operations"].append(("upload", url, mime, filename, kwargs))
        return f"media-{filename}"

    def send(*args):
        observed["operations"].append(("send", *args))
        observed["accepted"].append(args)
        return {"messages": [{"id": f"accepted-{len(observed['accepted'])}"}]}

    monkeypatch.setattr(main, "upload_chatwoot_attachment_to_meta", upload)
    monkeypatch.setattr(main, "send_whatsapp_media", send)

    main.process_chatwoot_event(event, event_id="message_created:91")

    expected_operations = []
    for index in range(1, 10):
        filename = f"photo-{index}.png"
        expected_operations.extend([
            ("upload", f"/rails/{filename}", "image/png", filename,
             {"attachment_created_at": event["created_at"]}),
            ("send", "57300", f"media-{filename}", "image", None, filename),
        ])
    assert observed["operations"] == expected_operations
    assert len(observed["accepted"]) == 9
    assert observed["statuses"] == [(44, 91, "sent")]
    assert observed["marked"] == [(("chatwoot", "message_created:91"), {})]
    assert observed["notes"] == []


@pytest.mark.parametrize("failure_stage", ["upload", "send"])
def test_partial_attachment_batch_stays_failed_without_replaying_accepted_image(
        monkeypatch, outgoing_attachment_batch, capsys, failure_stage):
    event, observed = outgoing_attachment_batch

    def upload(url, mime, filename, **kwargs):
        observed["operations"].append(("upload", filename))
        if filename == "photo-2.png" and failure_stage == "upload":
            return None
        return f"media-{filename}"

    def send(*args):
        observed["operations"].append(("send", args[-1]))
        if args[-1] == "photo-2.png":
            raise ProviderError("meta", "send_media", None, message="transport error")
        observed["accepted"].append(args)
        return {"messages": [{"id": "accepted-first-image"}]}

    monkeypatch.setattr(main, "upload_chatwoot_attachment_to_meta", upload)
    monkeypatch.setattr(main, "send_whatsapp_media", send)

    with pytest.raises(ProviderError):
        main.process_chatwoot_event(event, event_id="message_created:91")

    expected_operations = [("upload", "photo-1.png"), ("send", "photo-1.png"), ("upload", "photo-2.png")]
    if failure_stage == "send":
        expected_operations.append(("send", "photo-2.png"))
    assert observed["operations"] == expected_operations
    assert observed["accepted"] == [("57300", "media-photo-1.png", "image", None, "photo-1.png")]
    assert observed["statuses"] == [(44, 91, "failed", "No se pudo entregar el mensaje a WhatsApp")]
    assert observed["marked"][-1][1]["status"] == "failed"
    assert observed["logs"] == []
    assert len(observed["notes"]) == 1
    assert observed["notes"][0][1] == {"is_private": True}
    assert "evitar duplicados" in observed["notes"][0][0][1]
    assert "[CHATWOOT DELIVERY] forwarded" not in capsys.readouterr().out

    # Chatwoot's Retry UI can emit a status update. It must not replay the
    # already accepted first image, including after an ambiguous second send.
    event.update(event="message_updated", status="sent")
    main.process_chatwoot_event(event, event_id="message_updated:91")
    assert observed["operations"] == expected_operations
    assert observed["statuses"] == [(44, 91, "failed", "No se pudo entregar el mensaje a WhatsApp")]


@pytest.mark.parametrize("business_id", ["tanaka", "velvet", "memos", "new_business"])
def test_agent_video_keeps_original_creation_time_and_waits_before_single_send(monkeypatch, business_id):
    event = payload()
    event["created_at"] = main.datetime.now(main.timezone.utc).isoformat()
    event["attachments"] = [{"data_url": "/rails/original.mp4", "content_type": "video/mp4", "file_type": "video"}]
    uploaded, sent, statuses = [], [], []
    monkeypatch.setattr(main.config, "BUSINESS_ID", business_id)
    monkeypatch.setattr(main, "get_phone_by_chatwoot_id", lambda _value: "57300")
    monkeypatch.setattr(main, "save_message_log", lambda *a: None)
    monkeypatch.setattr(main, "upload_chatwoot_attachment_to_meta", lambda *a, **k: uploaded.append((a, k)) or "video-media")
    monkeypatch.setattr(main, "send_whatsapp_media", lambda *a: sent.append(a) or True)
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *a, **k: None)
    monkeypatch.setattr(chatwoot_api, "update_message_status", lambda *a: statuses.append(a))

    main.process_chatwoot_event(event)

    assert uploaded == [(("/rails/original.mp4", "video/mp4", "original.mp4"),
                         {"attachment_created_at": event["created_at"]})]
    assert sent == [("57300", "video-media", "video", "agent reply", "original.mp4")]
    assert statuses == [(44, 91, "sent")]


@pytest.mark.parametrize("attachments", [
    [{"file_type": "video"}],
    [{"file_type": "video", "thumb_url": "https://private.example/thumbnail?secret=DO_NOT_LOG"}],
    [{"data_url": "/valid.png"}, {"thumb_url": "https://private.example/DO_NOT_LOG"}],
])
def test_missing_original_attachment_fails_before_any_send_and_without_payload_leak(monkeypatch, capsys, attachments):
    event = payload()
    event["attachments"] = attachments
    statuses, notes, logs = [], [], []
    monkeypatch.setattr(main, "get_phone_by_chatwoot_id", lambda _value: "57300")
    monkeypatch.setattr(main, "save_message_log", lambda *a: logs.append(a))
    monkeypatch.setattr(main, "upload_chatwoot_attachment_to_meta", lambda *a, **k: pytest.fail("no original can be uploaded"))
    monkeypatch.setattr(main, "send_whatsapp_media", lambda *a: pytest.fail("no part can be sent"))
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *a, **k: None)
    monkeypatch.setattr(chatwoot_api, "update_message_status", lambda *a: statuses.append(a))
    monkeypatch.setattr(chatwoot_api, "send_message_to_chatwoot", lambda *a, **k: notes.append((a, k)))

    with pytest.raises(ProviderError, match="attachment_original_url_missing"):
        main.process_chatwoot_event(event)

    assert statuses == [(44, 91, "failed", "No se pudo entregar el mensaje a WhatsApp")]
    assert not logs
    assert len(notes) == 1 and notes[0][1] == {"is_private": True}
    assert "DO_NOT_LOG" not in capsys.readouterr().out


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
    monkeypatch.setattr(main, "get_media_asset", fake_get)
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
    monkeypatch.setattr(main, "upload_chatwoot_attachment_to_meta", lambda *a, **k: None)
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *a, **k: marked.append((a, k)))
    monkeypatch.setattr(chatwoot_api, "update_message_status", lambda *a: statuses.append(a))
    notes = []
    monkeypatch.setattr(chatwoot_api, "send_message_to_chatwoot", lambda *a, **k: notes.append((a, k)))

    with pytest.raises(ProviderError):
        main.process_chatwoot_event(event, event_id="message_created:91")
    assert marked[-1][1]["status"] == "failed"
    assert statuses == [(44, 91, "failed", "No se pudo entregar el mensaje a WhatsApp")]
    assert len(notes) == 1 and notes[0][1] == {"is_private": True}


@pytest.mark.parametrize("status", ["sent", "delivered"])
def test_chatwoot_api_message_status_uses_api_inbox_update_endpoint(monkeypatch, status):
    captured = {}

    def fake_put(url, **kwargs):
        captured.update(url=url, json=kwargs["json"])
        return Response(200, {"id": 91, "status": status})

    monkeypatch.setattr(chatwoot_api, "put", fake_put)

    assert chatwoot_api.update_message_status(44, 91, status) is not None
    assert captured == {
        "url": "https://app.chatwoot.com/api/v1/accounts/10/conversations/44/messages/91",
        "json": {"status": status},
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
    # Execution revalidates the window through queue_client after reservation;
    # that clock must agree with the worker clock, independent of test run time.
    monkeypatch.setattr(queue_client, "datetime", Frozen)
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


def test_state_read_failure_after_reservation_recovers_before_send(guarded_followups, monkeypatch):
    f = guarded_followups
    states = iter([{"is_paused": False}, None])
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *a: next(states))
    result = main.send_scheduled_follow_up("57300", f.token, "mensaje", "CATALOG_SENT", 1, "", f.expiry)
    assert result["status"] == "state_unavailable_before_send"
    assert len(f.queued) == 1
    assert f.queued[0][1]["preflight_retry"] == 1
    assert f.sent == []


def test_ambiguous_provider_result_cannot_retry_even_if_store_already_says_retry(guarded_followups, monkeypatch):
    f = guarded_followups
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *a: (_ for _ in ()).throw(ProviderError("meta", "send", None)))
    monkeypatch.setattr(main, "finish_follow_up_attempt", lambda *a, **k: {"status": "retry", "retry_at": f.now.timestamp() + 60})
    result = main.send_scheduled_follow_up("57300", f.token, "mensaje", "CATALOG_SENT", 1, "", f.expiry)
    assert result["status"] == "provider_result_ambiguous_manual_review"
    assert f.queued == []


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


@pytest.fixture
def direct_response_recovery(monkeypatch):
    from contextlib import contextmanager

    observed = {"posts": [], "logs": [], "paused": [], "handoffs": [], "marked": [], "locked": False}
    state = {"is_paused": False, "chatwoot_conversation_id": None, "current_state": "GREETING"}

    @contextmanager
    def locked(phone):
        observed["locked"] = True
        try:
            yield
        finally:
            observed["locked"] = False

    def log(phone, role, content, **kwargs):
        assert observed["locked"]
        observed["logs"].append((role, content, {**main.current_trace(), **kwargs.get("metadata", {})}))

    def pause(phone, reason):
        assert observed["locked"]
        observed["paused"].append(reason)
        state.update(is_paused=True, current_state="HUMAN_HANDOFF")

    def handoff(*args, **kwargs):
        assert observed["locked"]
        observed["handoffs"].append(main.current_trace())
        return {"status": "created", "conversation_id": 123}

    turn = types.SimpleNamespace(response="Con mucho gusto", requested_files=[],
        send_files_before_response=False, follow_up_message=None)
    monkeypatch.setattr(main, "phone_lock", locked)
    monkeypatch.setattr(main, "_current_job_id", lambda: "synthetic-response-job")
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *args: state)
    monkeypatch.setattr(main, "process_message_logic", lambda *args: turn)
    monkeypatch.setattr(main, "save_message_log", log)
    monkeypatch.setattr(main, "pause_bot_for_handoff", pause)
    monkeypatch.setattr(main, "_create_handoff_ticket_if_needed", handoff)
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *args, **kwargs: observed["marked"].append((args, kwargs)))
    monkeypatch.setattr(main, "schedule_follow_up_sequence", lambda *args, **kwargs: pytest.fail("failed response cannot schedule followups"))
    return state, observed


@pytest.mark.parametrize("failure,outcome,transport_reason", [
    (requests.exceptions.ReadTimeout("token=never-log-this https://example.invalid/private"), "unknown", "read_timeout_delivery_unknown"),
    (requests.exceptions.ConnectionError("private transport cause"), "unknown", "connection_error_delivery_unknown"),
    (requests.exceptions.SSLError("private TLS cause"), "unknown", "tls_error_delivery_unknown"),
    (requests.exceptions.Timeout("private timeout cause"), "unknown", "transport_error_delivery_unknown"),
    (requests.exceptions.ConnectTimeout("private connect cause"), "not_sent", "connection_timeout_before_send"),
    (Response(503), "unknown", ""),
    (Response(400, {"error": {"code": 131047, "message": "rejected"}}), "not_sent", "rejected"),
])
def test_closed_thanks_delivery_error_has_no_second_send_or_emergency_handoff(
        monkeypatch, capsys, direct_response_recovery, failure, outcome, transport_reason):
    state, observed = direct_response_recovery

    def post(*args, **kwargs):
        observed["posts"].append(kwargs["json"])
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(main, "post", post)
    received = main.datetime.now(main.timezone.utc)
    with main.message_trace(provider_message_id="wamid.preceding-catalog"):
        main.process_whatsapp_message("573001234567", "Cliente sintético", "Gracias por la información",
            event_id="synthetic-thanks", customer_message_received_at=received)
    assert len(observed["posts"]) == 1
    assert observed["paused"] == observed["handoffs"] == []
    assert not state["is_paused"]
    assert not any(role == "model" for role, _, _ in observed["logs"])
    failure_log = next(row for row in observed["logs"] if row[1].startswith("ERROR DE ENTREGA META:"))
    assert failure_log[2]["delivery_outcome"] == outcome
    assert failure_log[2]["source_event_id"] == "synthetic-thanks"
    assert failure_log[2]["received_at"] == received.isoformat()
    assert failure_log[2]["job_id"] == "synthetic-response-job"
    assert failure_log[2]["provider_message_id"] == ""
    assert observed["marked"][-1][1]["status"] == "failed"
    events = [json.loads(line.split(" ", 1)[1]) for line in capsys.readouterr().out.splitlines()
              if line.startswith("[OPERATIONAL_EVENT]")]
    delivery = next(event for event in events if event["event"] == "RESPONSE_DELIVERY_FAILED")
    assert delivery["transport_reason"] == transport_reason
    assert delivery["provider_message_id"] == ""
    assert delivery["business_id"] == main.config.BUSINESS_ID
    assert delivery["source_event_id"] == "synthetic-thanks"
    assert "deployment_sha" in delivery
    rendered = json.dumps(events) + str(observed["logs"]) + str(observed["marked"])
    assert "never-log-this" not in rendered and "private" not in rendered
    assert "wamid.preceding-catalog" not in rendered


@pytest.mark.parametrize("business_id", ["memos", "tanaka", "velvet", "future_brand"])
@pytest.mark.parametrize("customer_text,pending", [
    ("Gracias por la información", {"current_state": "PAYMENT_PENDING"}),
    ("Gracias por la información", {"pending_order": {"products": ["producto sintético"]}}),
    ("Gracias, ya pagué", {}),
    ("Gracias, ¿cuánto vale?", {}),
])
def test_pending_request_delivery_failure_keeps_human_review_without_resending(
        monkeypatch, direct_response_recovery, business_id, customer_text, pending):
    state, observed = direct_response_recovery
    state.update(pending)
    monkeypatch.setattr(main.config, "BUSINESS_ID", business_id)

    def post(*args, **kwargs):
        observed["posts"].append(kwargs["json"])
        raise requests.exceptions.ReadTimeout()

    monkeypatch.setattr(main, "post", post)
    main.process_whatsapp_message("CO.SyntheticSender", "Cliente sintético", customer_text,
        event_id="synthetic-pending", customer_message_received_at=main.datetime.now(main.timezone.utc))
    assert len(observed["posts"]) == len(observed["paused"]) == len(observed["handoffs"]) == 1
    assert state["is_paused"]
    assert "puede haber sido aceptado" in observed["paused"][0]
    assert observed["posts"][0]["recipient"] == "CO.SyntheticSender"
    assert observed["handoffs"][0]["source_event_id"] == "synthetic-pending"
    assert observed["marked"][-1][1]["status"] == "failed"


def test_failure_to_acquire_phone_lock_cannot_pause_another_workers_conversation(monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def unavailable(phone):
        raise TimeoutError("synthetic lock unavailable")
        yield

    marked = []
    monkeypatch.setattr(main, "phone_lock", unavailable)
    monkeypatch.setattr(main, "_process_whatsapp_message_unlocked", lambda *a, **k: pytest.fail("lock required"))
    monkeypatch.setattr(main, "pause_bot_for_handoff", lambda *a: pytest.fail("cannot mutate unlocked state"))
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *a, **k: marked.append(k))
    main.process_whatsapp_message("573001234567", "Cliente sintético", "consulta", event_id="locked-event")
    assert marked == [{"status": "failed", "error": "TimeoutError"}]


def test_text_failure_after_accepted_catalog_does_not_inherit_catalog_message_id(monkeypatch, direct_response_recovery):
    state, observed = direct_response_recovery
    turn = types.SimpleNamespace(response="Te comparto las opciones", requested_files=["catalogo_synthetic"],
        send_files_before_response=True, follow_up_message=None)

    def catalog(*args):
        main.update_trace(provider_message_id="wamid.synthetic-catalog")
        return True, None

    def post(*args, **kwargs):
        observed["posts"].append(kwargs["json"])
        raise requests.exceptions.ReadTimeout()

    monkeypatch.setattr(main, "process_message_logic", lambda *args: turn)
    monkeypatch.setattr(main, "deliver_presaved_file", catalog)
    monkeypatch.setattr(main, "post", post)
    main.process_whatsapp_message("573001234567", "Cliente sintético", "Quiero el catálogo",
        event_id="synthetic-catalog-turn", customer_message_received_at=main.datetime.now(main.timezone.utc))
    assert len(observed["posts"]) == 1
    accepted = [row for row in observed["logs"] if row[0] == "model"]
    assert [(row[1], row[2]["provider_message_id"]) for row in accepted] == [
        ("[Archivo enviado: catalogo_synthetic]", "wamid.synthetic-catalog")]
    failed = next(row for row in observed["logs"] if row[1].startswith("ERROR DE ENTREGA META:"))
    assert failed[2]["delivery_outcome"] == "unknown"
    assert failed[2]["provider_message_id"] == ""
    assert state["is_paused"]


def test_non_delivery_error_still_creates_emergency_case_under_original_lock_and_trace(monkeypatch, direct_response_recovery):
    state, observed = direct_response_recovery

    def failed_logic(*args):
        raise RuntimeError("synthetic processing failure")

    monkeypatch.setattr(main, "process_message_logic", failed_logic)
    monkeypatch.setattr(main, "post", lambda *args, **kwargs: observed["posts"].append(kwargs["json"]) or Response(200))
    main.process_whatsapp_message("573001234567", "Cliente sintético", "Consulta pendiente",
        event_id="synthetic-emergency", customer_message_received_at=main.datetime.now(main.timezone.utc))
    assert len(observed["paused"]) == len(observed["handoffs"]) == 1
    assert observed["handoffs"][0]["source_event_id"] == "synthetic-emergency"
    assert len(observed["posts"]) == 1
    assert "necesita revisión" in observed["posts"][0]["text"]["body"]
    assert state["is_paused"]
