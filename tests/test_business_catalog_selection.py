"""Regression checks for one-business-per-deployment configuration."""

from pathlib import Path

from test_bot_audit_contract import routed_bot as _routed_bot_fixture

routed_bot = _routed_bot_fixture


def test_config_has_one_generic_catalog_variable():
    source = Path("config.py").read_text(encoding="utf-8")
    assert 'PRESAVED_FILES_JSON = os.getenv("PRESAVED_FILES_JSON", "[]")' in source
    assert "catalogo_memos =" not in source
    assert "catalogo_tanaka =" not in source


def test_bot_uses_deployment_business_for_prompt_and_catalog():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert 'load_file_catalog(config.PRESAVED_FILES_JSON, "PRESAVED_FILES_JSON")' in source
    assert 'config.BUSINESS_ID / "system_instruction.txt"' in source
    assert "link=config.catalog_public_url()" in source
    assert "has_successful_file_delivery(phone, file_id)" in source


def test_catalog_selection_is_instruction_driven_and_deduplicates_deliveries(routed_bot, monkeypatch):
    route, _ = routed_bot
    monkeypatch.setattr(route.module, "has_successful_file_delivery", lambda *_: True)
    result = route("Recomiéndame algo", {
        "response": "Opciones para tu pedido", "trigger_handoff": False,
        "requested_files": ["catalogo_memos", "catalogo_memos", "unconfigured-file"],
    })
    assert result.requested_files == []


def test_main_only_logs_successful_file_delivery():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "delivered, technical_error = deliver_presaved_file(sender_phone, file_id)" in source
    assert "if delivered:" in source
    assert "ERROR enviando archivos" in source


def test_main_uses_business_id_in_catalog_filename():
    source = Path("main.py").read_text(encoding="utf-8")
    assert 'resolved_filename = f"catalogo-{config.BUSINESS_ID}-{digest}.{catalog_extension}"' in source
