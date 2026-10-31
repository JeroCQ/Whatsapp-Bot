"""Keep the advisor's handoff context current after the final bot reply."""

import types

import pytest

from test_chatwoot_integration import chatwoot_api, main
from provider_errors import ProviderError


@pytest.fixture
def handoff_reply_runtime(monkeypatch):
    state = types.SimpleNamespace(
        customer={"is_paused": False, "chatwoot_conversation_id": None},
        reply="Te comunico con el equipo.\nTu pedido queda pendiente de confirmar el domicilio.",
        requested_files=[],
        handoff_requested=True,
        initial_status="created",
        send_error=None,
        feedback_error=None,
        feedback_result={"id": 900},
        sent=[], notes=[], logs=[], summaries=[], events=[], sequence=[],
    )

    def model(*args):
        if state.handoff_requested:
            state.customer.update(is_paused=True, handoff_reason="Revisar domicilio")
        return types.SimpleNamespace(
            response=state.reply, requested_files=state.requested_files, send_files_before_response=False,
            follow_up_message=None, follow_up_delay_minutes=120,
            handoff_requested=state.handoff_requested,
        )

    def create_case(*args, **kwargs):
        if not args[2].get("is_paused"):
            return {"status": "not_requested"}
        state.sequence.append("create_case")
        state.summaries.append([dict(row) for row in state.logs])
        if len(state.summaries) == 1 and state.initial_status != "created":
            return {"status": state.initial_status}
        state.customer["chatwoot_conversation_id"] = 73
        return {"status": "created", "conversation_id": 73}

    def send(_phone, text):
        state.sequence.append("send_customer")
        state.sent.append(text)
        if state.send_error is not None:
            raise state.send_error
        return True

    def save(_phone, role, content, **kwargs):
        state.sequence.append(f"save_{role}")
        state.logs.append({"role": role, "content": content, "metadata": kwargs.get("metadata", {})})

    def feedback(conversation_id, content, **kwargs):
        state.sequence.append("private_feedback")
        state.notes.append((conversation_id, content, kwargs))
        if state.feedback_error is not None:
            raise state.feedback_error
        return state.feedback_result

    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *args: dict(state.customer))
    monkeypatch.setattr(main, "process_message_logic", model)
    monkeypatch.setattr(main, "_create_handoff_ticket_if_needed", create_case)
    monkeypatch.setattr(main, "send_whatsapp_message", send)
    monkeypatch.setattr(main, "save_message_log", save)
    monkeypatch.setattr(main, "get_message_logs", lambda *args, **kwargs: list(state.logs))
    monkeypatch.setattr(main, "update_follow_up_preferences", lambda *args: None)
    monkeypatch.setattr(main, "invalidate_follow_up", lambda *args: None)
    monkeypatch.setattr(main, "schedule_follow_up_sequence", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "operational_event", lambda *args, **kwargs: state.events.append((args, kwargs)))
    monkeypatch.setattr(chatwoot_api, "send_message_to_chatwoot", feedback)
    return state


def _process_reply():
    return main._process_whatsapp_message_unlocked(
        "573000000001", "Cliente sintético", "Quiero confirmar el pedido",
        customer_message_received_at=main.datetime.now(main.timezone.utc),
    )


@pytest.mark.parametrize("business", ["memos", "tanaka", "velvet", "future_brand"])
def test_handoff_advisor_sees_the_exact_final_reply_after_meta_accepts_it(
        handoff_reply_runtime, monkeypatch, business):
    state = handoff_reply_runtime
    monkeypatch.setattr(main.config, "BUSINESS_ID", business)
    state.reply = "Tu pedido queda con nuestro equipo.\n" + "Texto que debe conservarse completo. " * 50

    _process_reply()

    assert state.sent == [state.reply]
    assert state.customer["is_paused"] is True
    assert len(state.notes) == 1
    conversation_id, note, flags = state.notes[0]
    assert conversation_id == 73
    assert flags.get("is_private") is True
    assert "**Continuación del resumen de handoff**" in note
    assert "**Último mensaje del bot al cliente:**" in note
    assert state.reply in note
    assert state.sequence.index("create_case") < state.sequence.index("send_customer")
    assert state.sequence.index("send_customer") < state.sequence.index("save_model")
    assert state.sequence.index("save_model") < state.sequence.index("private_feedback")
    assert not any(row["role"] == "model" for row in state.summaries[0])
    assert "WhatsApp aceptó el envío" in note
    assert "no se confirma su recepción" in note
    context_events = [fields for args, fields in state.events if args == ("HANDOFF_REPLY_CONTEXT",)]
    assert context_events[0]["result"] == "context_added"
    assert state.reply not in str(context_events)


def test_rejected_meta_response_never_appears_as_a_sent_handoff_reply(handoff_reply_runtime):
    state = handoff_reply_runtime
    state.send_error = ProviderError("meta", "send_text", 400, code="2", message="Service temporarily unavailable")

    with pytest.raises(ProviderError):
        _process_reply()

    assert len(state.sent) == 1
    assert state.notes == []
    assert not any(row["role"] == "model" for row in state.logs)
    assert state.customer["is_paused"] is True


@pytest.mark.parametrize("feedback_failure", ["returns_none", "raises"])
def test_private_context_failure_does_not_resend_the_customer_message(
        handoff_reply_runtime, feedback_failure):
    state = handoff_reply_runtime
    if feedback_failure == "raises":
        state.feedback_error = RuntimeError("synthetic private-note error")
    else:
        state.feedback_result = None

    _process_reply()

    assert state.sent == [state.reply]
    assert len(state.notes) == 1
    assert state.customer["is_paused"] is True
    assert any(row["role"] == "model" and row["content"] == state.reply for row in state.logs)
    context_events = [fields for args, fields in state.events if args == ("HANDOFF_REPLY_CONTEXT",)]
    assert context_events[0]["result"] == ("context_error" if feedback_failure == "raises" else "not_confirmed")


def test_observability_failure_after_private_note_cannot_repeat_customer_reply(handoff_reply_runtime, monkeypatch):
    state = handoff_reply_runtime

    def events(name, **kwargs):
        if name == "HANDOFF_REPLY_CONTEXT":
            raise RuntimeError("synthetic observability error")

    monkeypatch.setattr(main, "operational_event", events)

    _process_reply()

    assert state.sent == [state.reply]
    assert len(state.notes) == 1
    assert state.customer["is_paused"] is True


def test_ordinary_model_response_does_not_create_handoff_continuation(handoff_reply_runtime):
    state = handoff_reply_runtime
    state.handoff_requested = False

    _process_reply()

    assert state.sent == [state.reply]
    assert not state.customer["is_paused"]
    assert state.notes == []


def test_pending_case_summary_uses_actual_fallback_instead_of_unsent_model_draft(handoff_reply_runtime):
    state = handoff_reply_runtime
    state.initial_status = "pending"
    unsent_draft = state.reply

    _process_reply()

    assert len(state.sent) == 1
    assert "pendiente" in state.sent[0]
    assert state.sent[0] != unsent_draft
    assert state.notes == []
    assert len(state.summaries) == 2
    final_history = state.summaries[-1]
    assert any(row["role"] == "model" and row["content"] == state.sent[0] for row in final_history)
    assert not any(row["role"] == "model" and row["content"] == unsent_draft for row in final_history)


@pytest.mark.parametrize("fallback_accepted", [True, False])
def test_catalog_failure_keeps_last_handoff_note_aligned_with_actual_customer_messages(
        handoff_reply_runtime, monkeypatch, fallback_accepted):
    state = handoff_reply_runtime
    state.requested_files = ["catalogo_portafolio"]
    monkeypatch.setattr(main, "deliver_presaved_file", lambda *args: (False, "synthetic_media_failure"))
    monkeypatch.setattr(main, "pause_bot_for_handoff", lambda _phone, reason: state.customer.update(
        is_paused=True, handoff_reason=reason,
    ))
    if not fallback_accepted:
        def send(_phone, text):
            state.sequence.append("send_customer")
            state.sent.append(text)
            if len(state.sent) == 2:
                raise ProviderError("meta", "send_text", 400, code="2", message="Service temporarily unavailable")
            return True

        monkeypatch.setattr(main, "send_whatsapp_message", send)

    _process_reply()

    # These are the existing model reply and catalog-failure notice, with no
    # extra customer-facing POST introduced by context synchronization.
    assert len(state.sent) == 2
    assert state.sent[0] == state.reply
    assert "no pude adjuntar el catálogo" in state.sent[1]
    assert len(state.notes) == (2 if fallback_accepted else 1)
    for index, (conversation_id, note, flags) in enumerate(state.notes):
        assert conversation_id == 73
        assert flags.get("is_private") is True
        assert state.sent[index] in note
    if fallback_accepted:
        second_send = [index for index, item in enumerate(state.sequence) if item == "send_customer"][1]
        second_note = [index for index, item in enumerate(state.sequence) if item == "private_feedback"][1]
        assert second_send < second_note
        assert [row["content"] for row in state.logs if row["role"] == "model"][-1] == state.sent[1]
    else:
        assert state.sent[1] not in state.notes[0][1]
        assert not any(row["role"] == "model" and row["content"] == state.sent[1] for row in state.logs)
    assert state.customer["is_paused"] is True


@pytest.mark.parametrize("handoff,reply", [
    (None, "Respuesta"),
    ({"status": "pending"}, "Respuesta"),
    ({"status": "created"}, "Respuesta"),
    ({"status": "created", "conversation_id": 73}, ""),
])
def test_context_helper_ignores_missing_confirmed_case_or_empty_reply(handoff_reply_runtime, handoff, reply):
    main._sync_handoff_reply_context(handoff, reply)

    assert handoff_reply_runtime.notes == []
    assert handoff_reply_runtime.sent == []
