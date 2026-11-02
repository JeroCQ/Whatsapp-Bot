"""Chatwoot mode is explicit and independent of the selected business."""

import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ENV = {
    "BUSINESS_ID": "future_business",
    "SUPABASE_URL": "https://synthetic.supabase.co",
    "SUPABASE_KEY": "synthetic-key",
    "WA_VERIFY_TOKEN": "synthetic-verify",
    "WA_TOKEN": "synthetic-whatsapp",
    "WA_PHONE_NUMBER_ID": "synthetic-meta-id",
    "GEMINI_API_KEY": "synthetic-gemini",
}
CHATWOOT_ENV = {
    "CHATWOOT_BASE_URL": "https://chatwoot.example.test",
    "CHATWOOT_API_TOKEN": "synthetic-chatwoot",
    "CHATWOOT_ACCOUNT_ID": "12",
    "CHATWOOT_INBOX_ID": "34",
    "CHATWOOT_WEBHOOK_SECRET": "synthetic-webhook",
}
BUSINESSES = ("tanaka", "memos", "velvet", "briosos", "future_business")


def load_config(env):
    spec = importlib.util.spec_from_file_location("_chatwoot_mode_config", ROOT / "config.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {**REQUIRED_ENV, **env}, clear=True):
        spec.loader.exec_module(module)
    return module.config


@pytest.mark.parametrize("business", BUSINESSES)
def test_missing_chatwoot_block_defaults_to_bot_only_for_every_business(business):
    config = load_config({"BUSINESS_ID": business})

    assert config.BUSINESS_ID == business
    assert config.CHATWOOT_ENABLED is False


@pytest.mark.parametrize("business", BUSINESSES)
def test_configured_business_keeps_chatwoot_enabled_by_default(business):
    config = load_config({**CHATWOOT_ENV, "BUSINESS_ID": business})

    assert config.CHATWOOT_ENABLED is True
    assert config.CHATWOOT_ACCOUNT_ID == "12"
    assert config.CHATWOOT_INBOX_ID == "34"


@pytest.mark.parametrize("business", BUSINESSES)
def test_explicit_false_ignores_all_copied_zero_chatwoot_values(business):
    copied_variables = (
        *CHATWOOT_ENV, "CHATWOOT_API_INBOX_WEBHOOK_SECRET", "CHATWOOT_ASSIGNEE_ID",
        "CHATWOOT_ASSIGNMENT_MODE", "CHATWOOT_API_URL", "CHATWOOT_ACCESS_TOKEN",
        "CHATWOOT_MAX_ATTACHMENT_BYTES", "CHATWOOT_MAX_DOCUMENT_BYTES",
    )
    config = load_config({**dict.fromkeys(copied_variables, "0"),
        "BUSINESS_ID": business, "CHATWOOT_ENABLED": "false"})

    assert config.CHATWOOT_ENABLED is False


@pytest.mark.parametrize("value", ("false", "0", "no", "off", " FaLsE "))
def test_explicit_false_spellings_disable_chatwoot(value):
    assert load_config({**CHATWOOT_ENV, "CHATWOOT_ENABLED": value}).CHATWOOT_ENABLED is False


@pytest.mark.parametrize("value", ("true", "1", "yes", "on", " TrUe "))
def test_explicit_true_spellings_enable_chatwoot(value):
    assert load_config({**CHATWOOT_ENV, "CHATWOOT_ENABLED": value}).CHATWOOT_ENABLED is True


@pytest.mark.parametrize("value", ("", " ", "auto", "disabled", "2", "truthy"))
def test_malformed_mode_is_rejected_instead_of_becoming_truthy(value):
    with pytest.raises(ValueError, match="CHATWOOT_ENABLED"):
        load_config({"CHATWOOT_ENABLED": value})


def test_explicit_enabled_mode_requires_a_complete_chatwoot_block():
    with pytest.raises(ValueError, match="Chatwoot requires"):
        load_config({"CHATWOOT_ENABLED": "true"})


@pytest.mark.parametrize("name", (
    "CHATWOOT_BASE_URL", "CHATWOOT_API_TOKEN", "CHATWOOT_ACCOUNT_ID", "CHATWOOT_INBOX_ID",
))
def test_partial_legacy_configuration_still_fails_validation(name):
    with pytest.raises(ValueError, match="Chatwoot requires"):
        load_config({name: CHATWOOT_ENV[name]})


@pytest.mark.parametrize("name", ("CHATWOOT_ACCOUNT_ID", "CHATWOOT_INBOX_ID", "CHATWOOT_ASSIGNEE_ID"))
def test_enabled_mode_still_rejects_zero_ids(name):
    with pytest.raises(ValueError, match=name):
        load_config({**CHATWOOT_ENV, "CHATWOOT_ENABLED": "true", name: "0"})


def test_zero_placeholders_are_not_an_implicit_disable_signal():
    with pytest.raises(ValueError):
        load_config(dict.fromkeys(CHATWOOT_ENV, "0"))
