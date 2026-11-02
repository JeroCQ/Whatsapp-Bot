"""Bot-only deployments never create or consume Chatwoot handoffs."""

import asyncio
import sys
import types

import pytest
from fastapi.testclient import TestClient

from test_bot_audit_contract import routed_bot
from test_chatwoot_integration import main, chatwoot_api, payload, _mock_followup_execution
from provider_errors import ProviderError


@pytest.fixture
def bot_only(monkeypatch):
    state = types.SimpleNamespace(
        customer={"is_paused": False, "current_state": "GREETING", "chatwoot_conversation_id": None},
        sent=[], logs=[], processed=[], normalized=[],
    )
    monkeypatch.setattr(main.config, "CHATWOOT_ENABLED", False)
    monkeypatch.setattr(main.config, "BUSINESS_ID", "briosos")
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *a: dict(state.customer))
    monkeypatch.setattr(main, "get_message_logs", lambda *a, **k: [])
    monkeypatch.setattr(main, "save_message_log", lambda *a, **k: state.logs.append((a, k)))
    monkeypatch.setattr(main, "update_follow_up_preferences", lambda *a, **k: None)
    monkeypatch.setattr(main, "invalidate_follow_up", lambda *a: None)
    monkeypatch.setattr(main, "pause_bot_for_handoff", lambda *a: None)
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *a: state.sent.append(a) or True)
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *a, **k: state.processed.append((a, k)))
    monkeypatch.setattr(main, "process_message_logic", lambda *a: types.SimpleNamespace(
        response="¿En qué puedo ayudarte?", requested_files=[], send_files_before_response=False,
        follow_up_message="", follow_up_delay_minutes=120, handoff_requested=False,
    ))
    monkeypatch.setattr(main, "ensure_bot_only_state", lambda phone, row: state.normalized.append((phone, row)) or row)

    def unexpected(*args, **kwargs):
        pytest.fail("disabled mode contacted Chatwoot/durable handoff state")

    for name in ("get_or_create_contact", "create_conversation", "send_message_to_chatwoot", "send_media_to_chatwoot"):
        monkeypatch.setattr(chatwoot_api, name, unexpected)
    for name in ("ensure_request", "pending_requests", "get_request", "reserve_request"):
        monkeypatch.setattr(main.handoff_delivery, name, unexpected)
    return state


def process_current(**kwargs):
    return main._process_whatsapp_message_unlocked(
        "573000000001", "Cliente sintético", "Hola", customer_message_received_at=main.datetime.now(main.timezone.utc),
        **kwargs,
    )


@pytest.mark.parametrize("business", ["briosos", "tanaka", "memos", "velvet", "future_brand"])
def test_any_bot_only_business_keeps_answering_without_handoff_storage(bot_only, monkeypatch, business):
    monkeypatch.setattr(main.config, "BUSINESS_ID", business)
    process_current()
    assert bot_only.sent == [("573000000001", "¿En qué puedo ayudarte?")]
    assert bot_only.normalized


def test_ticket_and_recovery_jobs_stop_before_durable_state(bot_only):
    assert main._create_handoff_ticket_if_needed("57300", "Cliente", {"is_paused": True}, None, "hola", None, None) == {"status": "disabled"}
    assert main.retry_pending_handoff("57300", "old-request", "briosos") is None
    assert main.recover_pending_handoffs() is None
    assert main.escalate_unattended_b2b_handoff("57300", 44, "B2B") is None
    assert main.recover_gemini_outage_handoffs("2026-01-01")["status"] == "disabled"


def test_startup_does_not_schedule_handoff_or_outage_recovery(bot_only, monkeypatch):
    monkeypatch.setattr(main.config, "GEMINI_OUTAGE_RECOVERY_SINCE", "2026-01-01T00:00:00Z")
    monkeypatch.setattr(main, "queue_enabled", lambda: False)
    monkeypatch.setattr(main.asyncio, "create_task", lambda *a: pytest.fail("disabled recovery was scheduled"))
    asyncio.run(main.log_deployment_version())


def test_disabled_webhook_rejects_before_signature_claim_or_enqueue(bot_only, monkeypatch):
    monkeypatch.setattr(main, "claim_webhook_event", lambda *a, **k: pytest.fail("disabled ingress claimed an event"))
    monkeypatch.setattr(main, "_dispatch_before_ack", lambda *a, **k: pytest.fail("disabled ingress queued an event"))
    assert TestClient(main.app).post("/chatwoot-webhook", json=payload()).status_code == 404
    assert main.process_claimed_chatwoot_event(payload(), event_id="old-event") == {"status": "disabled"}
    assert main.process_chatwoot_event(payload(), event_id="old-event") == {"status": "disabled"}


def test_manual_handoff_is_clear_operator_conflict_without_customer_mutations(bot_only):
    client = TestClient(main.app)
    result = client.post("/api/manual-handoff?client_name=briosos", headers={"X-Dashboard-API-Key": "dashboard-secret"},
                         json={"phone_number": "573000000001", "customer_name": "Cliente", "reason": "Atención manual"})
    assert result.status_code == 409
    assert not bot_only.sent
    assert client.post("/api/manual-handoff?client_name=other_brand", headers={"X-Dashboard-API-Key": "dashboard-secret"},
                       json={"phone_number": "573000000001", "reason": "Atención manual"}).status_code == 422


def test_audio_failure_keeps_customer_active_and_offers_text(bot_only, monkeypatch):
    monkeypatch.setattr(chatwoot_api, "download_meta_media", lambda *a: (b"audio", "audio/ogg"))
    monkeypatch.setattr(main, "transcribe_audio_message", lambda *a: None)
    process_current(media_id="synthetic-audio", media_type="audio", mime_type="audio/ogg")
    assert len(bot_only.sent) == 1
    assert "escribirme" in bot_only.sent[0][1]
    assert "equipo" not in bot_only.sent[0][1]
    assert bot_only.customer["is_paused"] is False


def test_file_failure_does_not_promise_a_nonexistent_advisor(bot_only, monkeypatch):
    monkeypatch.setattr(main, "process_message_logic", lambda *a: types.SimpleNamespace(
        response="Aquí tienes el archivo", requested_files=["catalogo_prueba"], send_files_before_response=False,
        follow_up_message="", follow_up_delay_minutes=120, handoff_requested=False,
    ))
    monkeypatch.setattr(main, "deliver_presaved_file", lambda *a: (False, "synthetic failure"))
    process_current()
    assert len(bot_only.sent) == 2
    assert "más adelante" in bot_only.sent[-1][1]
    assert "equipo" not in bot_only.sent[-1][1]
    assert bot_only.customer["is_paused"] is False


def test_ambiguous_meta_text_failure_is_not_replayed_in_bot_only_mode(bot_only):
    error = ProviderError("meta", "send_text", None, message="read_timeout_delivery_unknown")
    expiry = (main.datetime.now(main.timezone.utc) + main.timedelta(hours=1)).isoformat()
    main._recover_whatsapp_processing_error("57300", "Cliente", "pedido", error, expiry)
    assert bot_only.sent == []


def test_generic_processing_error_uses_retry_without_handoff(bot_only):
    expiry = (main.datetime.now(main.timezone.utc) + main.timedelta(hours=1)).isoformat()
    main._recover_whatsapp_processing_error("57300", "Cliente", "consulta", RuntimeError("synthetic failure"), expiry)
    assert len(bot_only.sent) == 1
    assert "intentarlo de nuevo" in bot_only.sent[0][1]
    assert "equipo" not in bot_only.sent[0][1]


def test_operator_pause_remains_respected_when_chatwoot_is_disabled(bot_only):
    bot_only.customer["is_paused"] = True
    process_current()
    assert bot_only.sent == []


def test_health_exposes_business_and_mode_without_configuration_values(bot_only, monkeypatch):
    monkeypatch.setattr(main, "queue_enabled", lambda: True)
    monkeypatch.setattr(main, "get_queue_stats", lambda: {"enabled": True, "queue": "whatsapp-events-briosos", "workers_seen": 1})
    body = TestClient(main.app).get("/health").json()
    assert body["business_id"] == "briosos"
    assert body["chatwoot_enabled"] is False
    assert "CHATWOOT_API_TOKEN" not in str(body)


@pytest.mark.parametrize("message", [
    "Un asesor revisará tu caso y se comunicará contigo.",
    "Te pondré en contacto con ventas.",
])
def test_old_human_promising_follow_up_is_skipped_after_disabling_handoffs(
        bot_only, routed_bot, monkeypatch, message):
    route, _ = routed_bot
    monkeypatch.setitem(sys.modules, "bot", route.module)
    monkeypatch.setattr(main.config, "CHATWOOT_ENABLED", False)
    monkeypatch.setattr(main, "follow_ups_enabled", lambda: True)
    monkeypatch.setattr(main, "reserve_follow_up_attempt", lambda *a, **k: pytest.fail("prohibited job reserved an attempt"))
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *a: pytest.fail("prohibited job queried customer state"))

    result = main.send_scheduled_follow_up("573000000001", "old-sequence", message)

    assert result == {"status": "human_service_disabled"}
    assert bot_only.sent == []


@pytest.mark.parametrize("enabled,message", [
    (False, "¿Quieres conocer las opciones del catálogo?"),
    (True, "Un asesor revisará tu caso y se comunicará contigo."),
])
def test_follow_up_keeps_ordinary_bot_only_and_enabled_mode_delivery(
        bot_only, routed_bot, monkeypatch, enabled, message):
    route, _ = routed_bot
    monkeypatch.setitem(sys.modules, "bot", route.module)
    monkeypatch.setattr(main.config, "CHATWOOT_ENABLED", enabled)
    monkeypatch.setattr(main, "follow_ups_enabled", lambda: True)
    _mock_followup_execution(monkeypatch)
    expiry = (main.datetime.now(main.timezone.utc) + main.timedelta(hours=1)).isoformat()

    result = main.send_scheduled_follow_up(
        "573000000001", "existing-sequence", message,
        meta_window_expires_at=expiry,
    )

    assert result == {"status": "customer_silent"}
    assert bot_only.sent == [("573000000001", message)]
