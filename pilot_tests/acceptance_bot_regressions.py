"""Bot/CRM compatibility on disposable PostgreSQL; Meta/Gemini are simulated."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from pathlib import Path

import psycopg
import pytest

from acceptance_pilot import PHONE, _command, _control, _post_incoming, _take, main
from acceptance_pilot import pilot as _pilot_fixture
from human_control_store import ControlError
from provider_errors import ProviderError

pilot = _pilot_fixture


def test_incremental_command_upgrade_matches_canonical_and_preserves_data(pilot):
    root = Path(__file__).parents[1]
    canonical = (root / "supabase/bootstrap.sql").read_text()
    upgrade = (root / "supabase/upgrade_existing_brand.sql").read_text()
    patch = (root / "supabase/upgrade_crm_bot_commands.sql").read_text()
    start = "CREATE OR REPLACE FUNCTION public.crm_control_dispatch("
    section = start + canonical.split(start, 1)[1].split("-- CRM CONTROL END", 1)[0]
    assert section in upgrade and section in patch
    _post_incoming(pilot)
    _take(pilot)
    before = _control(pilot)
    with psycopg.connect(pilot.bot_dsn, autocommit=True) as connection:
        connection.execute(patch)
        connection.execute(patch)
    after = _control(pilot)
    assert after["owner"] == before["owner"] and after["version"] == before["version"]
    assert after["confirmed"] and after["is_paused"]
    assert _command(pilot, "close", "SYNTHETIC-after-upgrade").status_code == 200


def _handoff_with_notice(monkeypatch):
    import database

    def decide(phone, *_args):
        database.pause_bot_for_handoff(phone, "SYNTHETIC human request")
        return SimpleNamespace(
            response="Tu solicitud requiere atención de un asesor.", requested_files=[],
            send_files_before_response=False, follow_up_message="", handoff_requested=True,
        )

    monkeypatch.setattr(main, "process_message_logic", decide)


def test_reset_uses_no_inference_preserves_audit_and_clears_bot_context(pilot):
    import database

    _post_incoming(pilot, body="SYNTHETIC previous order")
    previous = _control(pilot)["version"]
    with psycopg.connect(pilot.bot_dsn, autocommit=True) as connection:
        connection.execute(
            "UPDATE conversation_states SET customer_data='{}'::jsonb || '{\"city\":\"SYNTHETIC\"}'::jsonb,"
            "order_summary='SYNTHETIC old order',follow_up_token='SYNTHETIC-old-token' WHERE phone_number=%s",
            (PHONE,),
        )
    calls, sends = len(pilot.llm_calls), len(pilot.meta.sent)
    event = "wamid.SYNTHETIC.reset"
    _post_incoming(pilot, message_id=event, body="/reset")
    assert len(pilot.llm_calls) == calls and len(pilot.meta.sent) == sends + 1
    assert "Conversación reiniciada" in pilot.meta.sent[-1]["payload"]["text"]["body"]
    assert _control(pilot)["version"] == previous + 1
    row = pilot.repository.table("conversation_states").select("*").eq("phone_number", PHONE).execute().data[0]
    assert row["customer_data"] == {} and row["order_summary"] is None and row["follow_up_token"] is None
    audit = pilot.repository.table("message_logs").select("*").eq("phone_number", PHONE).execute().data
    assert any(item["content"] == "SYNTHETIC previous order" for item in audit)
    assert sum(item["metadata"].get("source") == "crm_reset" for item in audit) == 1
    assert all(item["content"] != "SYNTHETIC previous order" for item in database.get_message_logs(PHONE, 50))
    command = "reset:" + hashlib.sha256(event.encode()).hexdigest()
    pilot.store.apply_control(PHONE, "reset", command, actor="bot", expected_version=previous)
    assert _control(pilot)["version"] == previous + 1
    with pytest.raises(ControlError, match="control_version_conflict"):
        pilot.store.begin_send(PHONE, "SYNTHETIC-old-job", "auto", expected_version=previous)


@pytest.mark.parametrize("owned", [False, True])
def test_reset_cannot_release_human_control(pilot, owned):
    _post_incoming(pilot)
    if owned:
        _take(pilot)
    before, calls, sends = _control(pilot), len(pilot.llm_calls), len(pilot.meta.sent)
    _post_incoming(pilot, message_id="wamid.SYNTHETIC.paused-reset", body="/reset")
    after = _control(pilot)
    assert after["phase"] == before["phase"] and after["version"] == before["version"] and after["is_paused"]
    assert len(pilot.llm_calls) == calls and len(pilot.meta.sent) == sends


def test_reset_does_not_bypass_an_unknown_send(pilot):
    _post_incoming(pilot, body="Hola")
    version = _control(pilot)["version"]
    pilot.store.begin_send(PHONE, "SYNTHETIC-unknown", "auto", expected_version=version)
    pilot.store.finish_send("SYNTHETIC-unknown", "unknown", phone=PHONE)
    sends, calls = len(pilot.meta.sent), len(pilot.llm_calls)
    _post_incoming(pilot, message_id="wamid.SYNTHETIC.unknown-reset", body="/reset")
    assert len(pilot.meta.sent) == sends and len(pilot.llm_calls) == calls
    assert _control(pilot)["version"] == version


def test_actual_handoff_can_acknowledge_once_without_releasing_pause(pilot, monkeypatch):
    _handoff_with_notice(monkeypatch)
    event = "wamid.SYNTHETIC.handoff-notice"
    _post_incoming(pilot, message_id=event)
    assert _control(pilot)["phase"] == "waiting" and _control(pilot)["is_paused"]
    assert len(pilot.meta.sent) == 1
    with main.message_trace(source_event_id=event):
        with pytest.raises(ProviderError):
            main._send_crm_handoff_notice(PHONE, "Tu solicitud requiere atención de un asesor.")
    assert len(pilot.meta.sent) == 1
    _take(pilot)
    with main.message_trace(source_event_id=event):
        with pytest.raises(ControlError):
            main._send_crm_handoff_notice(PHONE, "No se debe enviar")
    assert len(pilot.meta.sent) == 1


def test_notice_requires_the_exact_business_conversation_and_handoff(pilot):
    _post_incoming(pilot)
    version = _control(pilot)["version"]
    with pytest.raises(ControlError, match="automatic_send_fenced"):
        pilot.store.begin_send(PHONE, "SYNTHETIC-ordinary", "auto", expected_version=version)
    with pytest.raises(ControlError, match="handoff_notice_not_authorized"):
        pilot.store.begin_send(PHONE, "SYNTHETIC-forged", "auto", actor="bot", expected_version=version,
                               text="SYNTHETIC", handoff_request_id="handoff:another-conversation")
    assert not pilot.meta.sent


def test_inflight_handoff_notice_is_coordinated_with_takeover(pilot, monkeypatch):
    _handoff_with_notice(monkeypatch)
    pilot.meta.mode = "blocking"
    with ThreadPoolExecutor(max_workers=2) as pool:
        incoming = pool.submit(_post_incoming, pilot, message_id="wamid.SYNTHETIC.notice-inflight")
        try:
            assert pilot.meta.entered.wait(5)
            result = _command(pilot, "take", "SYNTHETIC-take-during-notice")
            assert result.status_code == 202 and result.json()["control"]["phase"] == "pausing"
            assert not result.json()["control"]["can_send"]
        finally:
            pilot.meta.release.set()
        incoming.result(timeout=10)
    assert _control(pilot)["confirmed"] and len(pilot.meta.sent) == 1


def test_ambiguous_handoff_notice_is_not_automatically_replayed(pilot, monkeypatch):
    _handoff_with_notice(monkeypatch)
    pilot.meta.mode = "ambiguous"
    event = "wamid.SYNTHETIC.notice-unknown"
    _post_incoming(pilot, message_id=event)
    assert len(pilot.meta.sent) == 1
    with main.message_trace(source_event_id=event):
        with pytest.raises(ProviderError):
            main._send_crm_handoff_notice(PHONE, "Tu solicitud requiere atención de un asesor.")
    assert len(pilot.meta.sent) == 1
    taken = _command(pilot, "take", "SYNTHETIC-take-after-unknown")
    assert taken.status_code == 202 and not taken.json()["control"]["can_send"]
