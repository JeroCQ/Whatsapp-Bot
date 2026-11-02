"""Advisor file commands use the live brand catalog without forwarding control text."""

import sys
import types

import pytest

from test_chatwoot_integration import chatwoot_api, fake_bot, main, payload
from file_catalog import PresavedFile


def _catalog_item(file_id="catalogo_portafolio", name="Portafolio"):
    return PresavedFile(
        id=file_id,
        description=f"Archivo público {name}",
        media_type="document",
        link="https://storage.example.test/brand/current-file.pdf",
        filename=f"{name}.pdf",
        default_caption=name,
    )


def _command(text="/archivos", *, private=True, sender_type="user", **changes):
    event = payload()
    event.update(content=text, private=private, sender={"id": 3, "type": sender_type})
    event.update(changes)
    return event


@pytest.fixture
def command_runtime(monkeypatch):
    """Stub every provider/database boundary; keep real dispatcher and window guard."""
    state = types.SimpleNamespace(
        catalog={"catalogo_portafolio": _catalog_item()},
        refreshes=[], delivered=[], notes=[], statuses=[], logs=[],
        pauses=[], invalidated=[], processed=[], mapped=[],
    )
    monkeypatch.setattr(main.config, "CHATWOOT_ACCOUNT_ID", 10)
    monkeypatch.setattr(main.config, "CHATWOOT_INBOX_ID", 20)
    monkeypatch.setattr(main.config, "BUSINESS_ID", "test_brand")
    monkeypatch.setitem(sys.modules, "bot", fake_bot)

    def refresh(**kwargs):
        state.refreshes.append(kwargs)
        return dict(state.catalog)

    def mapped(conversation_id):
        state.mapped.append(conversation_id)
        return "573000000001"

    def feedback(conversation_id, text, **kwargs):
        state.notes.append((conversation_id, text, kwargs))
        return {"id": 92}

    monkeypatch.setattr(fake_bot, "refresh_managed_catalogs", refresh, raising=False)
    monkeypatch.setattr(main, "FILE_CATALOG", state.catalog)
    monkeypatch.setattr(main, "get_phone_by_chatwoot_id", mapped)
    monkeypatch.setattr(main, "get_message_logs", lambda *args, **kwargs: [
        {"role": "user", "content": "Consulta", "metadata": {
            "received_at": main.datetime.now(main.timezone.utc).isoformat(),
        }},
    ])
    monkeypatch.setattr(main, "pause_bot_for_human_activity", lambda *args: state.pauses.append(args))
    monkeypatch.setattr(main, "invalidate_follow_up", lambda *args: state.invalidated.append(args))
    monkeypatch.setattr(main, "save_message_log", lambda *args, **kwargs: state.logs.append((args, kwargs)))
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *args, **kwargs: state.processed.append((args, kwargs)))
    monkeypatch.setattr(main, "operational_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "deliver_presaved_file", lambda *args: state.delivered.append(args) or (True, None))
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *args: pytest.fail("control text must never reach WhatsApp"))
    monkeypatch.setattr(main, "send_whatsapp_media", lambda *args, **kwargs: pytest.fail("use the managed-file sender"))
    monkeypatch.setattr(chatwoot_api, "send_message_to_chatwoot", feedback)
    monkeypatch.setattr(chatwoot_api, "update_message_status", lambda *args, **kwargs: state.statuses.append((args, kwargs)))
    return state


def _private_feedback(state):
    assert state.notes, "the advisor needs visible private feedback"
    assert all(kwargs.get("is_private") is True for _, _, kwargs in state.notes)
    return "\n".join(text for _, text, _ in state.notes)


@pytest.mark.parametrize("brand", ["memos", "tanaka", "velvet", "future_brand"])
def test_file_list_uses_only_the_selected_business_catalog(command_runtime, monkeypatch, brand):
    state = command_runtime
    monkeypatch.setattr(main.config, "BUSINESS_ID", brand)
    file_id = f"catalogo_{brand}_vigente"
    state.catalog = {file_id: _catalog_item(file_id, f"Portafolio {brand}")}
    monkeypatch.setattr(main, "get_phone_by_chatwoot_id", lambda *_args: pytest.fail("listing must not map a customer"))
    monkeypatch.setattr(main, "get_message_logs", lambda *_args, **_kwargs: pytest.fail("listing must not read customer history"))

    main.process_chatwoot_event(_command(), event_id="message_created:91")

    note = _private_feedback(state)
    assert f"/catalogo {file_id}" in note
    assert f"Portafolio {brand}" in note
    assert "https://" not in note
    assert state.refreshes == [{"strict": True}]
    assert not state.delivered
    assert not state.pauses
    assert not state.invalidated


@pytest.mark.parametrize("text", ["/archivos", " /ARCHIVOS ", "/catalogo"])
@pytest.mark.parametrize("private", [True, False])
def test_common_discovery_commands_return_private_help_without_sending(command_runtime, text, private):
    state = command_runtime

    main.process_chatwoot_event(_command(text, private=private))

    assert "/catalogo catalogo_portafolio" in _private_feedback(state)
    assert not state.delivered
    assert not state.pauses
    assert not state.invalidated


def test_file_list_refreshes_newly_managed_files_without_restart(command_runtime):
    state = command_runtime
    main.process_chatwoot_event(_command(), event_id="message_created:91")
    state.catalog = {"catalogo_nuevo": _catalog_item("catalogo_nuevo", "Archivo nuevo")}
    event = _command(id=93)

    main.process_chatwoot_event(event, event_id="message_created:93")

    first, second = [text for _, text, _ in state.notes]
    assert "catalogo_portafolio" in first
    assert "catalogo_nuevo" in second
    assert "catalogo_portafolio" not in second
    assert len(state.refreshes) == 2
    assert not state.delivered


def test_empty_ready_catalog_gives_private_feedback(command_runtime):
    state = command_runtime
    state.catalog = {}

    main.process_chatwoot_event(_command())

    _private_feedback(state)
    assert not state.delivered
    assert not state.pauses


def test_large_catalog_listing_does_not_lose_later_files(command_runtime):
    state = command_runtime
    state.catalog = {
        f"catalogo_archivo_{index}": _catalog_item(f"catalogo_archivo_{index}", f"Archivo {index}")
        for index in range(45)
    }

    main.process_chatwoot_event(_command())

    feedback = _private_feedback(state)
    for file_id in state.catalog:
        assert f"/catalogo {file_id}" in feedback
    assert not state.delivered
    assert not state.pauses


@pytest.mark.parametrize("private", [True, False])
def test_managed_file_command_preserves_private_and_public_compatibility(command_runtime, private):
    state = command_runtime

    main.process_chatwoot_event(_command("/catalogo catalogo_portafolio", private=private))

    assert state.delivered == [("573000000001", "catalogo_portafolio")]
    assert state.refreshes == [{"strict": True}]
    assert state.pauses == [("573000000001", 44)]
    assert state.invalidated == [("573000000001",)]
    assert "Portafolio" in _private_feedback(state)


@pytest.mark.parametrize("text", [
    "/catalogo https://example.test/file.pdf",
    "/catalogo ../otra_marca",
    "/catalogo catalogo_portafolio extra",
    "/archivos extra",
])
@pytest.mark.parametrize("private", [True, False])
def test_malformed_reserved_commands_do_not_leak_to_customer(command_runtime, text, private):
    state = command_runtime

    main.process_chatwoot_event(_command(text, private=private))

    _private_feedback(state)
    assert not state.delivered
    assert not state.pauses
    assert not state.invalidated


def test_unknown_file_is_not_looked_up_in_another_business(command_runtime):
    state = command_runtime

    main.process_chatwoot_event(_command("/catalogo catalogo_otra_marca"))

    _private_feedback(state)
    assert not state.delivered
    assert not state.pauses
    assert not state.invalidated


def test_command_with_an_attached_file_returns_help_without_upload(command_runtime):
    state = command_runtime
    event = _command("/catalogo catalogo_portafolio", attachments=[{
        "file_type": "document", "data_url": "https://storage.example.test/unrelated.pdf",
    }])

    main.process_chatwoot_event(event)

    _private_feedback(state)
    assert not state.refreshes
    assert not state.delivered
    assert not state.pauses


@pytest.mark.parametrize("sender_type", ["contact", "agent_bot", "unknown", ""])
def test_private_commands_require_a_human_advisor_sender(command_runtime, sender_type):
    state = command_runtime

    main.process_chatwoot_event(_command(sender_type=sender_type))

    assert not state.refreshes
    assert not state.notes
    assert not state.delivered


def test_private_commands_accept_case_insensitive_user_type(command_runtime):
    state = command_runtime

    main.process_chatwoot_event(_command(sender_type="User"))

    assert state.refreshes == [{"strict": True}]
    _private_feedback(state)


@pytest.mark.parametrize("command", ["/archivos", "/catalogo catalogo_portafolio"])
def test_incoming_customer_cannot_execute_advisor_commands(command_runtime, command):
    state = command_runtime

    main.process_chatwoot_event(_command(command, private=False, message_type="incoming", sender_type="contact"))

    assert not state.refreshes
    assert not state.notes
    assert not state.delivered


@pytest.mark.parametrize("field,value", [("account", 99), ("inbox", 99)])
def test_commands_reject_another_account_or_inbox_before_catalog_access(command_runtime, field, value):
    state = command_runtime
    event = payload(**{field: value})
    event.update(content="/archivos", private=True, sender={"id": 3, "type": "user"})

    main.process_chatwoot_event(event)

    assert not state.refreshes
    assert not state.notes
    assert not state.delivered


def test_normal_private_note_does_not_become_a_customer_message(command_runtime):
    state = command_runtime

    main.process_chatwoot_event(_command("El cliente está pendiente de asesoría."))

    assert not state.refreshes
    assert not state.notes
    assert not state.delivered


def test_bot_private_feedback_does_not_execute_itself_again(command_runtime):
    state = command_runtime
    main.process_chatwoot_event(_command())
    feedback = state.notes[-1][1]
    initial_refreshes = len(state.refreshes)
    initial_notes = len(state.notes)

    main.process_chatwoot_event(_command(feedback, id=92), event_id="message_created:92")

    assert len(state.refreshes) == initial_refreshes
    assert len(state.notes) == initial_notes
    assert not state.delivered


def test_unmapped_conversation_cannot_send_a_managed_file(command_runtime, monkeypatch):
    state = command_runtime
    monkeypatch.setattr(main, "get_phone_by_chatwoot_id", lambda *_args: None)

    main.process_chatwoot_event(_command("/catalogo catalogo_portafolio"))

    _private_feedback(state)
    assert not state.delivered
    assert not state.pauses


@pytest.mark.parametrize("history", [[], [{"role": "user", "metadata": {}}], [
    {"role": "user", "metadata": {"received_at": "2020-01-01T12:00:00+00:00"}},
]])
def test_missing_or_expired_customer_window_blocks_before_any_upload(command_runtime, monkeypatch, history):
    state = command_runtime
    monkeypatch.setattr(main, "get_message_logs", lambda *_args, **_kwargs: history)

    main.process_chatwoot_event(_command("/catalogo catalogo_portafolio"))

    _private_feedback(state)
    assert not state.delivered


def test_catalog_refresh_failure_does_not_use_stale_files(command_runtime, monkeypatch):
    state = command_runtime

    def unavailable(**_kwargs):
        raise RuntimeError("catalog metadata unavailable")

    monkeypatch.setattr(fake_bot, "refresh_managed_catalogs", unavailable)

    main.process_chatwoot_event(_command("/catalogo catalogo_portafolio"))

    _private_feedback(state)
    assert not state.delivered
    assert not state.pauses


def test_provider_failure_is_visible_privately_without_customer_error(command_runtime, monkeypatch):
    state = command_runtime

    def rejected(*args):
        state.delivered.append(args)
        return False, "meta_media_upload_failed"

    monkeypatch.setattr(main, "deliver_presaved_file", rejected)

    main.process_chatwoot_event(_command("/catalogo catalogo_portafolio"))

    assert len(state.delivered) == 1
    _private_feedback(state)


def test_duplicate_webhook_does_not_send_the_file_twice(command_runtime, monkeypatch):
    state = command_runtime
    claimed = set()

    def claim(source, event_id, *_args):
        key = (source, event_id)
        if key in claimed:
            return False
        claimed.add(key)
        return True

    monkeypatch.setattr(main, "claim_webhook_event", claim)
    event = _command("/catalogo catalogo_portafolio")

    main.process_claimed_chatwoot_event(event, event_id="message_created:91")
    main.process_claimed_chatwoot_event(event, event_id="message_created:91")

    assert state.delivered == [("573000000001", "catalogo_portafolio")]
    assert len(state.notes) == 1


def test_failed_feedback_after_send_cannot_repeat_the_meta_delivery(command_runtime, monkeypatch):
    state = command_runtime
    claimed = set()

    def claim(source, event_id, *_args):
        key = (source, event_id)
        if key in claimed:
            return False
        claimed.add(key)
        return True

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("Chatwoot feedback unavailable")

    monkeypatch.setattr(main, "claim_webhook_event", claim)
    monkeypatch.setattr(chatwoot_api, "send_message_to_chatwoot", unavailable)
    event = _command("/catalogo catalogo_portafolio")

    main.process_claimed_chatwoot_event(event, event_id="message_created:91")
    main.process_claimed_chatwoot_event(event, event_id="message_created:91")

    assert state.delivered == [("573000000001", "catalogo_portafolio")]


@pytest.mark.parametrize("private", [True, False])
def test_local_log_failure_after_acceptance_does_not_report_send_failure_or_retry(command_runtime, monkeypatch, private):
    state = command_runtime

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("local logging unavailable")

    monkeypatch.setattr(main, "save_message_log", unavailable)

    main.process_chatwoot_event(_command("/catalogo catalogo_portafolio", private=private))

    assert state.delivered == [("573000000001", "catalogo_portafolio")]
    assert "aceptó" in _private_feedback(state)
    assert all(args[2] == "sent" for args, _kwargs in state.statuses)
