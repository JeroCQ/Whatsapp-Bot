from importlib.util import module_from_spec, spec_from_file_location
import base64
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location("check_business_setup", ROOT / "scripts" / "check_business_setup.py")
PREFLIGHT = module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)


def environment(business="arbitrary_new-brand"):
    return {
        "BUSINESS_ID": business,
        "SUPABASE_URL": "https://isolated.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "secret-service-role-value",
        "WA_VERIFY_TOKEN": "secret-verify-value", "WA_TOKEN": "secret-meta-token-value",
        "WA_PHONE_NUMBER_ID": "1234567890", "GEMINI_API_KEY": "secret-gemini-key-value",
        "REDIS_URL": "redis://default:secret-redis-password@private.internal:6379/0",
        "QUEUE_NAME": f"whatsapp-events-{business}",
        "CHATWOOT_ENABLED": "false", "FOLLOW_UPS_ENABLED": "false",
    }


def instruction(root, business="arbitrary_new-brand"):
    client = root / "src" / "clients" / business
    client.mkdir(parents=True)
    (client / "system_instruction.txt").write_text("Use only confirmed information for this business.", encoding="utf-8")
    return client


def policy():
    return {
        "timezone": "America/Bogota",
        "contact_hours": {"days": [0, 1], "start": "08:00", "end": "18:00"},
        "operating_hours": {"days": [0, 1], "start": "09:00", "end": "17:00"},
    }


def test_arbitrary_brand_needs_no_other_brand_data_and_no_external_services(tmp_path):
    instruction(tmp_path)
    result = PREFLIGHT.check_setup(environment(), tmp_path)
    assert result["ok"] is True
    assert result["offline_only"] is True
    assert result["external_services_verified"] is False


def test_legacy_service_role_key_fallback_does_not_require_a_new_secret(tmp_path):
    instruction(tmp_path)
    env = environment()
    env["SUPABASE_KEY"] = env.pop("SUPABASE_SERVICE_ROLE_KEY")
    result = PREFLIGHT.check_setup(env, tmp_path)
    assert result["ok"] is True
    assert result["configured"]["SUPABASE_KEY"] is True
    assert result["configured"]["SUPABASE_SERVICE_ROLE_KEY"] is False
    assert env["SUPABASE_KEY"] not in json.dumps(result)


@pytest.mark.parametrize("key_name", ["SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY"])
@pytest.mark.parametrize("key_type", ["publishable", "anon_jwt"])
def test_known_public_supabase_keys_fail_with_no_secret_output(tmp_path, key_name, key_type):
    instruction(tmp_path)
    env = environment()
    env.pop("SUPABASE_SERVICE_ROLE_KEY")
    payload = base64.urlsafe_b64encode(json.dumps({"role": "anon"}).encode()).decode().rstrip("=")
    env[key_name] = "sb_publishable_secret-sentinel" if key_type == "publishable" else f"header.{payload}.secret-signature"
    result = PREFLIGHT.check_setup(env, tmp_path)
    assert result["ok"] is False
    assert "supabase_key_not_public" in result["failed_checks"]
    assert env[key_name] not in json.dumps(result)


@pytest.mark.parametrize("name,value,check", [
    ("QUEUE_NAME", "whatsapp-events-velvet", "queue_matches_business"),
    ("GITHUB_SI_PATH", "src/clients/velvet/system_instruction.txt", "github_si_path_matches_business"),
    ("SUPABASE_URL", "https://isolated.supabase.co/rest/v1", "supabase_root_url"),
    ("REDIS_URL", "${{Redis.REDIS_URL}}", "redis_url_resolved_format"),
    ("WA_TOKEN", "0", "configured.WA_TOKEN"),
    ("RUN_WORKER_IN_WEB", "false", "embedded_worker_topology"),
    ("CHATWOOT_ENABLED", "n", "chatwoot_mode_explicit"),
    ("CHATWOOT_ENABLED", "y", "chatwoot_mode_explicit"),
])
def test_copied_or_unresolved_settings_fail(tmp_path, name, value, check):
    instruction(tmp_path)
    env = environment()
    env[name] = value
    result = PREFLIGHT.check_setup(env, tmp_path)
    assert result["ok"] is False
    assert check in result["failed_checks"]


def test_missing_or_path_traversal_business_never_reads_another_instruction(tmp_path):
    instruction(tmp_path)
    env = environment("../arbitrary_new-brand")
    result = PREFLIGHT.check_setup(env, tmp_path)
    assert result["checks"]["business_id_format"] is False
    assert result["checks"]["system_instruction_nonempty"] is False


@pytest.mark.parametrize("mode,assignee,passes", [("automatic", "", True), ("garbage", "", False), ("fixed", "0", False)])
def test_chatwoot_enabled_requires_valid_assignment_configuration(tmp_path, mode, assignee, passes):
    instruction(tmp_path)
    env = environment()
    env.update(
        CHATWOOT_ENABLED="true", CHATWOOT_BASE_URL="https://chatwoot.example.com",
        CHATWOOT_API_TOKEN="secret-chatwoot-token", CHATWOOT_ACCOUNT_ID="1", CHATWOOT_INBOX_ID="2",
        CHATWOOT_API_INBOX_WEBHOOK_SECRET="secret-chatwoot-signature",
        CHATWOOT_ASSIGNMENT_MODE=mode, CHATWOOT_ASSIGNEE_ID=assignee,
    )
    assert PREFLIGHT.check_setup(env, tmp_path)["ok"] is passes


def test_followups_require_explicit_policy_while_disabled_still_validates_supplied_policy(tmp_path):
    client = instruction(tmp_path)
    env = environment()
    env["FOLLOW_UPS_ENABLED"] = "true"
    assert "follow_up_calendar_explicit" in PREFLIGHT.check_setup(env, tmp_path)["failed_checks"]
    (client / "business_policy.json").write_text(json.dumps(policy()), encoding="utf-8")
    assert PREFLIGHT.check_setup(env, tmp_path)["ok"] is True
    env["FOLLOW_UPS_ENABLED"] = "false"
    env["BUSINESS_POLICY_JSON"] = '{"timezone": "invalid-secret-zone"}'
    assert "effective_business_policy_valid" in PREFLIGHT.check_setup(env, tmp_path)["failed_checks"]


def test_output_never_echoes_secrets_even_in_malformed_urls_and_policy(tmp_path):
    instruction(tmp_path)
    env = environment()
    env["REDIS_URL"] = "redis://secret-user:secret-password@invalid:secret-port"
    env["BUSINESS_POLICY_JSON"] = "secret-malformed-json"
    encoded = json.dumps(PREFLIGHT.check_setup(env, tmp_path))
    for secret in ("secret-user", "secret-password", "secret-port", "secret-malformed-json", *[
        env[name] for name in ("SUPABASE_SERVICE_ROLE_KEY", "WA_TOKEN", "WA_VERIFY_TOKEN", "GEMINI_API_KEY")
    ]):
        assert secret not in encoded


@pytest.mark.parametrize("raw", ["", " ", '{"follow_up_min_spacing_seconds": 1e999}'])
def test_malformed_local_policy_fails_safely_even_when_followups_disabled(tmp_path, raw):
    client = instruction(tmp_path)
    (client / "business_policy.json").write_text(raw, encoding="utf-8")
    result = PREFLIGHT.check_setup(environment(), tmp_path)
    assert result["ok"] is False
    assert "effective_business_policy_valid" in result["failed_checks"]


def test_empty_policy_does_not_approve_default_calendar_for_live_followups(tmp_path):
    instruction(tmp_path)
    env = environment()
    env.update(FOLLOW_UPS_ENABLED="true", BUSINESS_POLICY_JSON="{}")
    assert "follow_up_calendar_explicit" in PREFLIGHT.check_setup(env, tmp_path)["failed_checks"]


def test_cli_reports_missing_environment_as_json_with_nonzero_exit():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_business_setup.py")],
        env={}, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert output["ok"] is False
    assert "configured.BUSINESS_ID" in output["failed_checks"]
    assert result.stderr == ""
