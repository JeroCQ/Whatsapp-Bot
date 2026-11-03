"""Provider selection is explicit, backward compatible and independent of brand."""

import sys
import types
from unittest.mock import patch

import pytest
from test_bot_audit_contract import routed_bot as _routed_bot_fixture
from test_bot_only_database import (
    bot_only_database as _bot_only_database_fixture,
)
from test_bot_only_database import (
    handoff_state,
)
from test_chatwoot_mode_config import CHATWOOT_ENV, load_config

from human_support import (
    human_handoffs_enabled,
    human_support_provider,
    uses_chatwoot,
    uses_crm,
)

bot_only_database = _bot_only_database_fixture
routed_bot = _routed_bot_fixture


@pytest.mark.parametrize(
    "configured,legacy,expected",
    [
        ("", True, "chatwoot"),
        ("", False, "bot_only"),
        ("chatwoot", False, "chatwoot"),
        ("crm", False, "crm"),
        ("crm", True, "crm"),
        ("bot_only", True, "bot_only"),
    ],
)
def test_effective_provider_selection(configured, legacy, expected):
    settings = types.SimpleNamespace(
        HUMAN_SUPPORT_PROVIDER=configured, CHATWOOT_ENABLED=legacy
    )
    assert human_support_provider(settings) == expected
    assert uses_chatwoot(settings) == (expected == "chatwoot")
    assert uses_crm(settings) == (expected == "crm")
    assert human_handoffs_enabled(settings) == (expected != "bot_only")


@pytest.mark.parametrize("business", ["tanaka", "memos", "velvet", "future_business"])
def test_crm_mode_ignores_copied_chatwoot_configuration(business):
    settings = load_config(
        {
            **dict.fromkeys(CHATWOOT_ENV, "0"),
            "BUSINESS_ID": business,
            "HUMAN_SUPPORT_PROVIDER": "crm",
            "CRM_CONTROL_ENABLED": "true",
            "CRM_BOT_CONTROL_SECRET": "synthetic-control-" + "x" * 40,
            "WA_APP_SECRET": "synthetic-app-" + "a" * 40,
        }
    )
    assert settings.CHATWOOT_ENABLED is False
    assert uses_crm(settings) and human_handoffs_enabled(settings)


@pytest.mark.parametrize("provider", ["none", "chat", "invalid", "1"])
def test_unknown_provider_fails_instead_of_falling_back(provider):
    with pytest.raises(ValueError, match="HUMAN_SUPPORT_PROVIDER"):
        load_config({"HUMAN_SUPPORT_PROVIDER": provider})


def test_existing_chatwoot_deployment_does_not_enable_crm_implicitly():
    settings = load_config(CHATWOOT_ENV)
    assert uses_chatwoot(settings)
    assert settings.CRM_CONTROL_ENABLED is False
    assert settings.CRM_SOURCE_READ_ENABLED is False


def test_explicit_chatwoot_requires_its_existing_credentials():
    with pytest.raises(ValueError, match="Chatwoot requires"):
        load_config({"HUMAN_SUPPORT_PROVIDER": "chatwoot", "CHATWOOT_ENABLED": "false"})


def test_explicit_bot_only_disables_a_copied_chatwoot_block():
    settings = load_config({**CHATWOOT_ENV, "HUMAN_SUPPORT_PROVIDER": "bot_only"})
    assert settings.CHATWOOT_ENABLED is False and not human_handoffs_enabled(settings)


def test_control_cannot_be_enabled_for_legacy_or_bot_only_provider():
    with pytest.raises(ValueError, match="requires HUMAN_SUPPORT_PROVIDER=crm"):
        load_config({"CRM_CONTROL_ENABLED": "true", "CRM_BOT_CONTROL_SECRET": "x" * 40})


def test_enabled_control_requires_dedicated_secret():
    with pytest.raises(ValueError, match="CRM_BOT_CONTROL_SECRET"):
        load_config({"HUMAN_SUPPORT_PROVIDER": "crm", "CRM_CONTROL_ENABLED": "true"})


def test_explicit_crm_cannot_start_without_an_enabled_control_path():
    with pytest.raises(ValueError, match="requires CRM_CONTROL_ENABLED=true"):
        load_config({"HUMAN_SUPPORT_PROVIDER": "crm"})


def test_crm_control_requires_the_meta_webhook_signing_secret():
    with pytest.raises(ValueError, match="WA_APP_SECRET"):
        load_config(
            {
                "HUMAN_SUPPORT_PROVIDER": "crm",
                "CRM_CONTROL_ENABLED": "true",
                "CRM_BOT_CONTROL_SECRET": "x" * 40,
            }
        )


def test_source_and_control_secrets_cannot_be_reused():
    with pytest.raises(ValueError, match="must be different"):
        load_config(
            {
                "HUMAN_SUPPORT_PROVIDER": "crm",
                "CRM_CONTROL_ENABLED": "true",
                "CRM_BOT_CONTROL_SECRET": "x" * 40,
                "CRM_SOURCE_READ_ENABLED": "true",
                "CRM_SOURCE_READ_SECRET": "x" * 40,
                "WA_APP_SECRET": "a" * 40,
            }
        )


def test_source_can_be_enabled_independently_for_an_authorized_read_pilot():
    settings = load_config(
        {"CRM_SOURCE_READ_ENABLED": "true", "CRM_SOURCE_READ_SECRET": "r" * 40}
    )
    assert settings.CRM_SOURCE_READ_ENABLED is True
    assert not settings.CRM_CONTROL_ENABLED


def test_crm_provider_keeps_human_pause_when_chatwoot_is_disabled(bot_only_database):
    fixture = bot_only_database
    fixture.config.HUMAN_SUPPORT_PROVIDER = "crm"
    state = handoff_state(
        chatwoot_conversation_id=None, control_phase="human", control_owner="employee"
    )
    assert fixture.module.ensure_bot_only_state(state["phone_number"], state) is state
    assert fixture.queries == []


@pytest.mark.parametrize("phase", ["waiting", "pausing", "human"])
def test_provider_flag_rollback_cannot_release_an_open_crm_case(
    bot_only_database, phase
):
    fixture = bot_only_database
    fixture.config.HUMAN_SUPPORT_PROVIDER = "bot_only"
    state = handoff_state(chatwoot_conversation_id=None, crm_control_phase=phase)
    assert fixture.module.ensure_bot_only_state(state["phone_number"], state) is state
    assert fixture.queries == []


def test_crm_handoff_uses_authoritative_operation_not_table_update(
    bot_only_database, monkeypatch
):
    fixture = bot_only_database
    fixture.config.HUMAN_SUPPORT_PROVIDER = "crm"
    calls = []
    store = types.ModuleType("human_control_store")
    store.apply_control = lambda *args, **kwargs: calls.append((args, kwargs)) or {
        "phase": "waiting",
        "is_paused": True,
    }
    trace = types.ModuleType("observability")
    trace.current_trace = lambda: {"source_event_id": "synthetic-incoming-1"}
    with patch.dict(
        sys.modules, {"human_control_store": store, "observability": trace}
    ):
        result = fixture.module.pause_bot_for_handoff(
            "573000000001", "Needs human attention"
        )
    assert result["is_paused"] is True
    assert fixture.queries == []
    assert calls == [
        (
            ("573000000001", "handoff"),
            {
                "command_id": "handoff:synthetic-incoming-1",
                "actor": "bot",
                "expected_version": None,
                "payload": {"reason": "Needs human attention"},
            },
        )
    ]


def test_crm_model_can_request_handoff_with_chatwoot_disabled(routed_bot, monkeypatch):
    route, pauses = routed_bot
    monkeypatch.setattr(
        route.module.config, "HUMAN_SUPPORT_PROVIDER", "crm", raising=False
    )
    monkeypatch.setattr(route.module.config, "CHATWOOT_ENABLED", False)
    turn = route(
        "Necesito ayuda para un pedido mayorista",
        {
            "response": "Un asesor revisará tu solicitud.",
            "trigger_handoff": True,
            "handoff_reason": "Cliente pide atención humana",
        },
    )
    assert turn.handoff_requested and len(pauses) == 1
    assert turn.follow_up_message == ""
