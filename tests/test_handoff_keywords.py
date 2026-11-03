"""Exercise configured handoff keywords without external AI, DB or messaging."""

import json

import pytest

from commercial_intents import human_handoff_reason
from follow_up_policy import load_business_policy
from test_bot_audit_contract import routed_bot


@pytest.mark.parametrize("text", [
    "asesor",
    "ASESOR",
    "¡Asesor!",
    "Quiero hablar con un asesor, por favor",
    "Necesito asesor y el catálogo",
])
def test_keyword_is_normalized_and_matches_a_complete_word(text):
    assert human_handoff_reason(text, ("asesor",)) == "HUMAN_REQUEST: palabra clave asesor"


@pytest.mark.parametrize("text", ["asesoramiento", "asesoria", "asesores", "hola", ""])
def test_substrings_and_unrelated_messages_do_not_request_a_human(text):
    assert human_handoff_reason(text, ("asesor",)) is None
    assert human_handoff_reason("asesor", ()) is None


def test_only_briosos_enables_the_keyword_in_its_local_policy():
    assert load_business_policy("briosos", {}).human_handoff_keywords == ("asesor",)
    for business in ("tanaka", "memos", "velvet", "future_isolated_business"):
        assert load_business_policy(business, {}).human_handoff_keywords == ()


@pytest.mark.parametrize("keywords", [None, "asesor", {}, [None], [1], [""], ["  "]])
def test_keyword_configuration_rejects_invalid_values(keywords):
    with pytest.raises(ValueError):
        load_business_policy("future_business", {
            "BUSINESS_POLICY_JSON": json.dumps({"human_handoff_keywords": keywords}),
        })


@pytest.mark.parametrize("routed_bot", ["briosos"], indirect=True)
@pytest.mark.parametrize("text", ["asesor", "¡ASESOR!", "Necesito un asesor y el catálogo"])
def test_briosos_keyword_pauses_without_inference(routed_bot, monkeypatch, text):
    route, pauses = routed_bot
    module = route.module
    monkeypatch.delenv("BUSINESS_POLICY_JSON", raising=False)
    monkeypatch.setattr(module.config, "HUMAN_SUPPORT_PROVIDER", "chatwoot", raising=False)
    monkeypatch.setattr(module, "_call_gemini", lambda *args: pytest.fail(
        "An explicit configured handoff must not depend on model output"
    ))

    turn = module.process_message_logic("57300", text)

    assert turn.handoff_requested
    assert turn.handoff_reason == "HUMAN_REQUEST: palabra clave asesor"
    assert pauses == [("57300", turn.handoff_reason)]
    assert turn.follow_up_message == ""
    assert turn.requested_files == []
    assert "requiere atención" in turn.response


@pytest.mark.parametrize("routed_bot", ["tanaka", "memos", "velvet"], indirect=True)
def test_other_brands_do_not_gain_an_automatic_keyword_handoff(routed_bot, monkeypatch):
    route, pauses = routed_bot
    monkeypatch.delenv("BUSINESS_POLICY_JSON", raising=False)
    monkeypatch.setattr(route.module.config, "HUMAN_SUPPORT_PROVIDER", "chatwoot", raising=False)

    turn = route("asesor", {"response": "Respuesta del modelo", "trigger_handoff": False})

    assert not turn.handoff_requested
    assert pauses == []
    assert turn.response == "Respuesta del modelo"


@pytest.mark.parametrize("routed_bot", ["briosos"], indirect=True)
def test_crm_provider_handles_the_keyword_with_chatwoot_disabled(routed_bot, monkeypatch):
    route, pauses = routed_bot
    module = route.module
    monkeypatch.delenv("BUSINESS_POLICY_JSON", raising=False)
    monkeypatch.setattr(module.config, "HUMAN_SUPPORT_PROVIDER", "crm", raising=False)
    monkeypatch.setattr(module.config, "CHATWOOT_ENABLED", False)
    monkeypatch.setattr(module, "_call_gemini", lambda *args: pytest.fail(
        "CRM keyword handoff must not call Gemini or depend on Chatwoot"
    ))

    turn = module.process_message_logic("57300", "asesor")

    assert turn.handoff_requested
    assert len(pauses) == 1
    assert turn.follow_up_message == ""


@pytest.mark.parametrize("routed_bot", ["briosos"], indirect=True)
def test_bot_only_mode_does_not_pause_for_the_keyword(routed_bot, monkeypatch):
    route, pauses = routed_bot
    module = route.module
    monkeypatch.delenv("BUSINESS_POLICY_JSON", raising=False)
    monkeypatch.setattr(module.config, "HUMAN_SUPPORT_PROVIDER", "bot_only", raising=False)
    monkeypatch.setattr(module.config, "CHATWOOT_ENABLED", False)
    monkeypatch.setattr(module, "ensure_bot_only_state", lambda phone, state: state)

    turn = route("asesor", {"response": "Puedo ayudarte por este chat.", "trigger_handoff": False})

    assert not turn.handoff_requested
    assert turn.handoff_reason == ""
    assert pauses == []


def test_future_business_can_configure_its_own_keyword(routed_bot, monkeypatch):
    route, pauses = routed_bot
    module = route.module
    monkeypatch.setattr(module.config, "BUSINESS_ID", "future_business")
    monkeypatch.setattr(module.config, "HUMAN_SUPPORT_PROVIDER", "chatwoot", raising=False)
    monkeypatch.setattr(module, "SYSTEM_INSTRUCTION", "Asistente de un negocio de pruebas.")
    monkeypatch.setenv("BUSINESS_POLICY_JSON", json.dumps({"human_handoff_keywords": ["operador"]}))
    monkeypatch.setattr(module, "_call_gemini", lambda *args: pytest.fail(
        "A future business must use the same configured keyword path"
    ))

    turn = module.process_message_logic("57300", "¡OPERADOR!")

    assert turn.handoff_requested
    assert turn.handoff_reason == "HUMAN_REQUEST: palabra clave operador"
    assert pauses == [("57300", turn.handoff_reason)]
