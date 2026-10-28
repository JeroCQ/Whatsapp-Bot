"""Run the actual model-output routing with synthetic Gemini/DB providers."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def routed_bot(monkeypatch, request):
    business = getattr(request, "param", "memos")
    for key, value in {"BUSINESS_ID": business, "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_KEY": "test-key", "WA_VERIFY_TOKEN": "test", "WA_TOKEN": "test",
        "WA_PHONE_NUMBER_ID": "test", "GEMINI_API_KEY": "test"}.items():
        monkeypatch.setenv(key, value)
    from config import config
    import database
    from google import genai
    from file_catalog import PresavedFile

    monkeypatch.setattr(config, "BUSINESS_ID", business)
    monkeypatch.setattr(config, "PRESAVED_FILES_JSON", "[]")
    monkeypatch.setattr(database, "get_catalog_assets", lambda: [])
    client_options = {}
    def client_factory(**kwargs):
        client_options.update(kwargs)
        return object()
    monkeypatch.setattr(genai, "Client", client_factory)
    path = Path(__file__).resolve().parents[1] / "bot.py"
    spec = importlib.util.spec_from_file_location("_audit_bot_contract", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    catalog_id = "catalogo_mochis_mayorista" if business == "velvet" else "catalogo_memos"
    description = "Mochis al por mayor" if business == "velvet" else "Catálogo general de quesos"
    module.FILE_CATALOG[catalog_id] = PresavedFile(catalog_id, description, "document", link="https://example.test/catalog.pdf")
    monkeypatch.setattr(module, "refresh_managed_catalogs", lambda: module.FILE_CATALOG)
    monkeypatch.setattr(module, "get_or_create_customer_state", lambda *a: {"is_paused": False, "current_state": "GREETING"})
    monkeypatch.setattr(module, "get_message_logs", lambda *a, **k: [])
    monkeypatch.setattr(module, "has_successful_file_delivery", lambda *a: False)
    monkeypatch.setattr(module, "update_conversation_memory", lambda *a: None)
    monkeypatch.setattr(module, "save_message_log", lambda *a: None)
    pauses = []
    monkeypatch.setattr(module, "pause_bot_for_handoff", lambda *a: pauses.append(a))

    def route(text, data):
        monkeypatch.setattr(module, "_call_gemini", lambda *a: types.SimpleNamespace(text=json.dumps(data)))
        return module.process_message_logic("57300", text)
    route.module = module
    route.client_options = client_options
    return route, pauses


def test_wholesale_handoff_survives_catalog_fallback(routed_bot):
    route, pauses = routed_bot
    turn = route("Eso es precios al por mayor", {"response": "Aquí tienes opciones", "trigger_handoff": False, "requested_files": []})
    assert turn.handoff_requested
    assert turn.handoff_reason.startswith("B2B_HIGH_VALUE:")
    assert len(pauses) == 1
    assert turn.follow_up_message == ""


def test_runtime_client_uses_milliseconds_and_one_sdk_attempt(routed_bot):
    route, _ = routed_bot
    options = route.client_options["http_options"]
    assert options.timeout == 20_000
    assert options.retry_options.attempts == 1


@pytest.mark.parametrize("routed_bot", ["memos", "tanaka", "velvet"], indirect=True)
def test_text_generation_uses_runtime_http_options_for_each_brand(routed_bot, monkeypatch):
    from google.genai import types as genai_types
    route, _ = routed_bot
    module = route.module
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(text=json.dumps({"response": "Respuesta sintética", "trigger_handoff": False}))
    monkeypatch.setattr(module, "client", types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate)))
    http_options = genai_types.HttpOptions(timeout=12_000)
    monkeypatch.setattr(module, "_call_gemini", lambda operation, call: call(http_options))

    turn = module.process_message_logic("57300", "Hola")

    assert turn.response == "Respuesta sintética"
    assert calls[0]["model"] == "gemini-flash-latest"
    assert calls[0]["config"].http_options.timeout == 12_000


def test_audio_generation_uses_the_same_runtime_deadline(routed_bot, monkeypatch):
    from google.genai import types as genai_types
    route, _ = routed_bot
    module = route.module
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(text="Transcripción sintética")
    monkeypatch.setattr(module, "client", types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate)))
    monkeypatch.setattr(module, "_call_gemini", lambda operation, call: call(genai_types.HttpOptions(timeout=8_000)))

    assert module.transcribe_audio_message(b"synthetic-voice") == "Transcripción sintética"
    assert calls[0]["config"].http_options.timeout == 8_000


def test_simple_catalog_request_still_resolves_automatically(routed_bot):
    route, pauses = routed_bot
    turn = route("Mándame el catálogo", {"response": "Aquí tienes", "trigger_handoff": True,
        "handoff_reason": "Catálogo no disponible", "requested_files": []})
    assert turn.requested_files == ["catalogo_memos"]
    assert not turn.handoff_requested
    assert pauses == []


def test_genuine_human_request_is_not_cancelled_by_available_catalog(routed_bot):
    route, pauses = routed_bot
    turn = route("Necesito asesor y el catálogo", {"response": "Lo revisa un asesor", "trigger_handoff": True,
        "handoff_reason": "Solicita atención humana", "requested_files": []})
    assert turn.handoff_requested
    assert pauses


def test_unknown_five_pound_block_cannot_be_denied_as_unavailable(routed_bot):
    route, pauses = routed_bot
    turn = route("Tienen bloques de cinco libras?", {"response": "No tenemos bloques de cinco libras", "trigger_handoff": False})
    assert turn.handoff_requested
    assert turn.handoff_reason.startswith("INFORMATION_VALIDATION:")
    assert "No tenemos" not in turn.response
    assert "precio" in turn.response
    assert pauses


def test_model_output_preserves_missing_versus_intentionally_empty(routed_bot):
    route, _ = routed_bot
    data = {"response": "Qué queso necesitas", "trigger_handoff": False}
    assert route("hola", data).follow_up_message is None
    assert route("hola", {**data, "follow_up_message": ""}).follow_up_message == ""


@pytest.mark.parametrize("routed_bot", ["velvet"], indirect=True)
def test_velvet_standard_wholesale_keeps_approved_self_service_policy(routed_bot):
    route, pauses = routed_bot
    turn = route("Quiero 50 mochis al por mayor", {"response": "50 unidades por $175.000", "trigger_handoff": False,
        "requested_files": ["catalogo_mochis_mayorista"]})
    assert not turn.handoff_requested
    assert pauses == []


@pytest.mark.parametrize("routed_bot", ["memos", "tanaka", "velvet"], indirect=True)
def test_exhausted_google_503_handoff_is_specific_correlated_and_private(routed_bot, monkeypatch, capsys):
    from google.genai.errors import ServerError
    from observability import message_trace
    route, pauses = routed_bot
    module = route.module
    def unavailable(*args):
        raise ServerError(503, {"error": {"code": 503, "message": "PRIVATE_PROVIDER_RESPONSE"}})
    monkeypatch.setattr(module, "_call_gemini", unavailable)
    with message_trace(source_event_id="synthetic-inbound", job_id="synthetic-job"):
        turn = module.process_message_logic("57300", "PRIVATE_CUSTOMER_MESSAGE")
    assert len(pauses) == 1 and pauses[0][0] == "57300"
    assert "Google temporalmente no disponible" in pauses[0][1]
    assert "HTTP 503" in pauses[0][1]
    assert "créditos" not in pauses[0][1]
    assert turn.requested_files == []
    assert turn.response == "Te comunico con nuestro equipo para continuar ayudándote."
    assert turn.handoff_requested and turn.handoff_reason == pauses[0][1]
    assert turn.follow_up_message == ""
    output = capsys.readouterr().out
    event = next(json.loads(line.split("] ", 1)[1]) for line in output.splitlines()
                 if line.startswith("[OPERATIONAL_EVENT]") and '"GEMINI_OPERATION_FAILED"' in line)
    assert event["reason"] == "provider_unavailable"
    assert event["provider_status"] == 503
    assert event["business_id"] == module.config.BUSINESS_ID
    assert event["source_event_id"] == "synthetic-inbound"
    assert event["job_id"] == "synthetic-job"
    assert event["model"] == "gemini-flash-latest"
    assert event["duration_ms"] >= 0
    assert "PRIVATE_PROVIDER_RESPONSE" not in output
    assert "PRIVATE_CUSTOMER_MESSAGE" not in output


def test_prepaid_credit_handoff_remains_distinct(routed_bot, monkeypatch):
    from google.genai.errors import ClientError
    route, pauses = routed_bot
    def depleted(*args):
        raise ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                          "message": "Your prepayment credits are depleted."}})
    monkeypatch.setattr(route.module, "_call_gemini", depleted)
    turn = route.module.process_message_logic("57300", "Hola")
    assert pauses == [("57300", "Gemini no respondió: no hay créditos prepagados")]
    assert turn.response == "Te comunico con nuestro equipo para continuar ayudándote."
    assert turn.handoff_requested and turn.follow_up_message == ""


@pytest.mark.parametrize("response", [None, "not JSON", "[]", '"some string"'])
def test_invalid_model_json_hands_off_with_format_reason(routed_bot, monkeypatch, response):
    route, pauses = routed_bot
    monkeypatch.setattr(route.module, "_call_gemini", lambda *a: types.SimpleNamespace(text=response))
    turn = route.module.process_message_logic("57300", "Hola")
    assert pauses == [("57300", "Gemini no respondió: respuesta de Google con formato inválido")]
    assert turn.response == "Te comunico con nuestro equipo para continuar ayudándote."
    assert turn.handoff_requested and turn.follow_up_message == ""


def test_audio_timeout_records_specific_failure_without_audio_or_provider_body(routed_bot, monkeypatch, capsys):
    import httpx
    route, _ = routed_bot
    def timeout(*args):
        raise httpx.ReadTimeout("PRIVATE_PROVIDER_RESPONSE")
    monkeypatch.setattr(route.module, "_call_gemini", timeout)
    assert route.module.transcribe_audio_message(b"PRIVATE_AUDIO") is None
    output = capsys.readouterr().out
    assert '"operation": "audio_transcription"' in output
    assert '"reason": "network_timeout"' in output
    assert "PRIVATE_PROVIDER_RESPONSE" not in output
    assert "PRIVATE_AUDIO" not in output


@pytest.mark.parametrize("routed_bot", ["memos", "tanaka", "velvet"], indirect=True)
def test_usage_records_sdk_counts_and_resolved_model_without_content(routed_bot, monkeypatch, capsys):
    from google.genai import types as genai_types
    route, _ = routed_bot
    response = genai_types.GenerateContentResponse(
        model_version="gemini-test-flash-001",
        usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=5000, candidates_token_count=300,
            thoughts_token_count=100, cached_content_token_count=4000, total_token_count=5400),
        candidates=[genai_types.Candidate(content=genai_types.Content(parts=[
            genai_types.Part(text=json.dumps({"response": "PRIVATE_MODEL_TEXT", "trigger_handoff": False}))]))],
    )
    monkeypatch.setattr(route.module, "_call_gemini", lambda *args: response)
    route.module.process_message_logic("57300", "PRIVATE_CUSTOMER_TEXT")
    output = capsys.readouterr().out
    event = next(json.loads(line.split("] ", 1)[1]) for line in output.splitlines()
                 if line.startswith("[OPERATIONAL_EVENT]") and '"GEMINI_USAGE"' in line)
    assert event["resolved_model"] == "gemini-test-flash-001"
    assert event["prompt_token_count"] == 5000
    assert event["cached_content_token_count"] == 4000
    assert event["thoughts_token_count"] == 100
    assert event["candidates_token_count"] == 300
    assert event["total_token_count"] == 5400
    assert event["business_id"] == route.module.config.BUSINESS_ID
    assert event["operation"] == "message_logic"
    assert "PRIVATE_MODEL_TEXT" not in output and "PRIVATE_CUSTOMER_TEXT" not in output


def test_usage_ignores_missing_or_unexpected_provider_fields(routed_bot, capsys):
    route, _ = routed_bot
    response = types.SimpleNamespace(model_version="https://private.invalid/token", usage_metadata=types.SimpleNamespace(
        prompt_token_count=True, total_token_count=-1, thoughts_token_count="PRIVATE_PAYLOAD"))
    route.module._record_gemini_usage(response, "audio_transcription", route.module.time.perf_counter())
    output = capsys.readouterr().out
    event = json.loads(next(line.split("] ", 1)[1] for line in output.splitlines() if '"GEMINI_USAGE"' in line))
    assert event["resolved_model"] == "unknown"
    assert not any(field in event for field in ("prompt_token_count", "total_token_count", "thoughts_token_count"))
    assert "private.invalid" not in output and "PRIVATE_PAYLOAD" not in output
