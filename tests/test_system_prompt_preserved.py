"""The runtime must load business knowledge from a replaceable file."""

from pathlib import Path


def test_business_prompt_is_not_hardcoded_in_bot_module():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert "BASE DE CONOCIMIENTO DE PRODUCTOS" not in source
    assert '_SYSTEM_INSTRUCTION_PATH.read_text(encoding="utf-8")' in source


def test_each_model_turn_includes_the_current_colombia_time():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert 'datetime.now(ZoneInfo("America/Bogota"))' in source
    assert "Fecha y hora local actual en Colombia: {colombia_time}" in source
