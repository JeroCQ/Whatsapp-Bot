"""Exercise bot-only model routing without Gemini, Supabase or Chatwoot traffic."""

import json
import types

import pytest

from test_bot_audit_contract import routed_bot


@pytest.fixture
def bot_only(routed_bot, monkeypatch):
    route, _ = routed_bot
    monkeypatch.setattr(route.module.config, "CHATWOOT_ENABLED", False)
    monkeypatch.setattr(route.module, "ensure_bot_only_state", lambda phone, state: state)
    return routed_bot


@pytest.mark.parametrize("routed_bot", ["briosos", "tanaka", "memos", "velvet"], indirect=True)
def test_model_handoff_is_disabled_for_each_brand(bot_only):
    route, pauses = bot_only
    turn = route("Necesito ayuda con mi pedido", {
        "response": "Cuéntame qué producto necesitas y te ayudo por aquí.",
        "trigger_handoff": True,
        "handoff_reason": "Solicita atención humana",
    })

    assert turn.response == "Cuéntame qué producto necesitas y te ayudo por aquí."
    assert not turn.handoff_requested
    assert turn.handoff_reason == ""
    assert pauses == []


@pytest.mark.parametrize("draft", [
    "Ya te transferí con un asesor para que te ayude.",
    "Nuestro equipo comercial revisará contigo los detalles.",
    "Un agente te contactará pronto.",
    "Tu solicitud ya fue remitida y te responderán pronto.",
    "Voy a pasarte con una persona de atención.",
    "Voy a pasarte con alguien que puede ayudarte.",
    "Te pondré en contacto con ventas.",
])
def test_stale_handoff_promises_are_replaced_even_without_model_flag(bot_only, draft):
    route, pauses = bot_only
    turn = route("Necesito ayuda", {
        "response": draft,
        "trigger_handoff": False,
        "follow_up_message": "Un asesor se comunicará contigo.",
    })

    assert turn.response == route.module._BOT_ONLY_FALLBACK_RESPONSE
    assert not turn.handoff_requested
    assert turn.follow_up_message == ""
    assert pauses == []


def test_ordinary_information_sharing_is_not_treated_as_handoff(bot_only):
    route, pauses = bot_only
    response = "Te pasaré el catálogo con las opciones disponibles. ¿Qué producto buscas?"

    turn = route("Cuáles opciones tienen?", {"response": response, "trigger_handoff": False})

    assert turn.response == response
    assert not turn.handoff_requested
    assert pauses == []


@pytest.mark.parametrize("business", ["briosos", "tanaka", "memos", "velvet", "future-isolated-brand"])
def test_b2b_continues_answering_and_selecting_available_files(bot_only, monkeypatch, business):
    from file_catalog import PresavedFile

    route, pauses = bot_only
    module = route.module
    monkeypatch.setattr(module.config, "BUSINESS_ID", business)
    module.FILE_CATALOG["catalogo_distibuidores"] = PresavedFile(
        "catalogo_distibuidores", "Información para distribuidores", "document",
        link="https://example.test/distributors.pdf",
    )

    turn = route("Quiero fabricación para terceros", {
        "response": "Puedo mostrarte la información disponible. ¿Qué cantidades buscas?",
        "trigger_handoff": False,
        "requested_files": [],
    })

    assert turn.response == "Puedo mostrarte la información disponible. ¿Qué cantidades buscas?"
    assert turn.requested_files == ["catalogo_distibuidores"]
    assert not turn.handoff_requested
    assert turn.handoff_reason == ""
    assert pauses == []


def test_unknown_presentation_has_no_false_availability_or_human_validation_promise(bot_only):
    route, pauses = bot_only
    turn = route("Tienen bloques de cinco libras?", {
        "response": "No tenemos bloques de cinco libras",
        "trigger_handoff": False,
    })

    assert "No tenemos" not in turn.response
    assert "información confirmada" in turn.response
    assert "disponibilidad" in turn.response
    assert "precio" in turn.response
    assert "equipo" not in turn.response
    assert not turn.handoff_requested
    assert pauses == []


@pytest.mark.parametrize("routed_bot", ["briosos", "memos", "tanaka", "velvet"], indirect=True)
def test_runtime_prompt_overrides_brand_escalation_rules(bot_only, monkeypatch):
    route, _ = bot_only
    module = route.module
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(text=json.dumps({"response": "¿Cómo te ayudo?", "trigger_handoff": False}))

    monkeypatch.setattr(module, "client", types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate)))
    monkeypatch.setattr(module, "_call_gemini", lambda operation, generate: generate(None))

    module.process_message_logic("57300", "Hola")

    prompt = calls[0]["contents"]
    instruction = calls[0]["config"].system_instruction
    assert instruction.endswith(module._BOT_ONLY_INSTRUCTION)
    assert "trigger_handoff=false" in instruction
    assert "Nunca anuncies, prometas" in instruction
    assert "solicita validación humana" not in prompt
    assert "aplicando rigurosamente las REGLAS ESTRICTAS DE ESCALAMIENTO" not in prompt
    assert "SOLO BOT" in prompt


@pytest.mark.parametrize("failure", ["unavailable", "timeout", "depleted", "invalid_json"])
def test_model_failure_retries_without_handoff_or_follow_up(bot_only, monkeypatch, capsys, failure):
    import httpx
    from google.genai.errors import ClientError, ServerError

    route, pauses = bot_only
    module = route.module

    def fail(*args):
        if failure == "unavailable":
            raise ServerError(503, {"error": {"code": 503, "message": "PRIVATE_PROVIDER_BODY"}})
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE_PROVIDER_BODY")
        if failure == "depleted":
            raise ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                              "message": "Your prepayment credits are depleted."}})
        return types.SimpleNamespace(text="not JSON")

    monkeypatch.setattr(module, "_call_gemini", fail)
    turn = module.process_message_logic("57300", "PRIVATE_CUSTOMER_MESSAGE")

    assert "inténtalo de nuevo" in turn.response
    assert "asesor" not in turn.response.lower()
    assert "gemini" not in turn.response.lower()
    assert not turn.handoff_requested
    assert turn.handoff_reason == ""
    assert turn.requested_files == []
    assert turn.follow_up_message == ""
    assert pauses == []
    output = capsys.readouterr().out
    assert '"event": "GEMINI_OPERATION_FAILED"' in output
    assert "PRIVATE_PROVIDER_BODY" not in output
    assert "PRIVATE_CUSTOMER_MESSAGE" not in output


def test_stale_handoff_is_normalized_before_pause_guard_and_keeps_memory(bot_only, monkeypatch):
    route, pauses = bot_only
    module = route.module
    old_state = {
        "is_paused": True, "current_state": "HUMAN_HANDOFF", "chatwoot_conversation_id": 24,
        "handoff_reason": "Anterior", "customer_data": {"nombre": "Ana", "ciudad": "Cali"},
        "order_summary": "Pedido confirmado: 2 productos",
    }
    normalized = {**old_state, "is_paused": False, "current_state": "GREETING",
                  "chatwoot_conversation_id": None, "handoff_reason": None}
    calls = []
    memory = []
    monkeypatch.setattr(module, "get_or_create_customer_state", lambda phone: old_state)
    monkeypatch.setattr(module, "ensure_bot_only_state", lambda phone, state: calls.append((phone, state)) or normalized)
    monkeypatch.setattr(module, "update_conversation_memory", lambda *args: memory.append(args))

    turn = route("Retomemos mi pedido", {"response": "Sigamos con tu pedido", "trigger_handoff": False})

    assert turn.response == "Sigamos con tu pedido"
    assert calls == [("57300", old_state)]
    assert memory == [("57300", old_state["customer_data"], old_state["order_summary"])]
    assert pauses == []


def test_bare_operator_pause_stays_respected_in_bot_only_mode(bot_only, monkeypatch):
    route, pauses = bot_only
    module = route.module
    state = {"is_paused": True, "current_state": "GREETING"}
    monkeypatch.setattr(module, "get_or_create_customer_state", lambda phone: state)
    monkeypatch.setattr(module, "get_message_logs", lambda *args, **kwargs: pytest.fail("paused conversation must not run"))

    assert route("Hola", {"response": "No debe enviarse", "trigger_handoff": False}) is None
    assert pauses == []


def test_enabled_mode_still_uses_handoff_and_does_not_normalize_state(routed_bot, monkeypatch):
    route, pauses = routed_bot
    module = route.module
    monkeypatch.setattr(module.config, "CHATWOOT_ENABLED", True)
    monkeypatch.setattr(module, "ensure_bot_only_state", lambda *args: pytest.fail("enabled mode owns human handoffs"))

    turn = route("Quiero fabricación para terceros", {"response": "Aquí tienes la información", "trigger_handoff": False})

    assert turn.handoff_requested
    assert turn.handoff_reason.startswith("B2B_HIGH_VALUE:")
    assert len(pauses) == 1
