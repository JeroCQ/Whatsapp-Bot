"""Regression checks for one-business-per-deployment configuration."""

from pathlib import Path


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


def test_main_uses_business_id_in_catalog_filename():
    source = Path("main.py").read_text(encoding="utf-8")
    assert 'resolved_filename = f"catalogo-{config.BUSINESS_ID}-{digest}.{catalog_extension}"' in source
