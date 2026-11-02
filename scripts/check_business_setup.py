#!/usr/bin/env python3
"""Offline onboarding checks. No network, runtime clients, writes or secret output."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Mapping
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
# This helper uses only the standard library and performs no work on import.
sys.path.insert(0, str(ROOT))
from follow_up_policy import load_business_policy


REQUIRED = (
    "BUSINESS_ID", "SUPABASE_URL", "WA_VERIFY_TOKEN",
    "WA_TOKEN", "WA_PHONE_NUMBER_ID", "GEMINI_API_KEY", "REDIS_URL", "QUEUE_NAME",
)
CHATWOOT_REQUIRED = (
    "CHATWOOT_BASE_URL", "CHATWOOT_API_TOKEN", "CHATWOOT_ACCOUNT_ID", "CHATWOOT_INBOX_ID",
)
_PLACEHOLDERS = {"", "0", "false", "none", "null", "changeme", "replace_me"}
_BUSINESS_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _value(environ: Mapping, name: str) -> str:
    return str(environ.get(name, "") or "").strip()


def _configured(environ: Mapping, name: str) -> bool:
    return _value(environ, name).lower() not in _PLACEHOLDERS


def _flag(environ: Mapping, name: str, default: bool | None) -> bool | None:
    raw = _value(environ, name).lower()
    if not raw:
        return default
    return True if raw in _TRUE else False if raw in _FALSE else None


def _url_valid(value: str, schemes: set[str], *, root_only: bool = False) -> bool:
    try:
        parts = urlsplit(value)
        valid = parts.scheme in schemes and bool(parts.hostname) and parts.port != 0
        if root_only:
            valid = valid and parts.path in {"", "/"} and not (
                parts.query or parts.fragment or parts.username or parts.password
            )
        return bool(valid)
    except (ValueError, TypeError):
        return False


def _not_public_supabase_key(value: str) -> bool:
    if value.startswith(("sb_publishable_", "sb_anon_")):
        return False
    parts = value.split(".")
    if len(parts) == 3:
        try:
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
            if isinstance(payload, dict) and payload.get("role") in ("anon", "authenticated"):
                return False
        except (ValueError, UnicodeError):
            pass
    # This is only recognition of known public key forms, never authentication.
    return True


def check_setup(environ: Mapping | None = None, root: Path = ROOT) -> dict:
    """Return only check names, static advice and booleans; never provider values."""
    environ = os.environ if environ is None else environ
    configured = {
        name: _configured(environ, name)
        for name in (*REQUIRED, "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY", *CHATWOOT_REQUIRED)
    }
    checks = {f"configured.{name}": configured[name] for name in REQUIRED}
    warnings = []
    business = _value(environ, "BUSINESS_ID")
    checks["business_id_format"] = bool(_BUSINESS_ID.fullmatch(business))
    checks["supabase_root_url"] = _url_valid(_value(environ, "SUPABASE_URL"), {"https"}, root_only=True)
    checks["redis_url_resolved_format"] = _url_valid(_value(environ, "REDIS_URL"), {"redis", "rediss"})
    phone_id = _value(environ, "WA_PHONE_NUMBER_ID")
    checks["meta_phone_id_format"] = bool(re.fullmatch(r"[0-9]+", phone_id)) and bool(phone_id.strip("0"))
    checks["queue_matches_business"] = (
        checks["business_id_format"] and _value(environ, "QUEUE_NAME") == f"whatsapp-events-{business}"
    )
    key_name = "SUPABASE_SERVICE_ROLE_KEY" if environ.get("SUPABASE_SERVICE_ROLE_KEY") else "SUPABASE_KEY"
    key = _value(environ, key_name)
    checks["supabase_server_key_present"] = configured[key_name]
    checks["supabase_key_not_public"] = _not_public_supabase_key(key)

    chatwoot = _flag(environ, "CHATWOOT_ENABLED", None)
    follow_ups = _flag(environ, "FOLLOW_UPS_ENABLED", True)
    checks["chatwoot_mode_explicit"] = chatwoot is not None
    checks["follow_up_flag_valid"] = follow_ups is not None
    if chatwoot:
        for name in CHATWOOT_REQUIRED:
            checks[f"configured.{name}"] = configured[name]
        checks["chatwoot_root_url"] = _url_valid(_value(environ, "CHATWOOT_BASE_URL"), {"https"}, root_only=True)
        checks["chatwoot_webhook_secret"] = any(
            _configured(environ, name)
            for name in ("CHATWOOT_WEBHOOK_SECRET", "CHATWOOT_API_INBOX_WEBHOOK_SECRET")
        )
        for name in ("CHATWOOT_ACCOUNT_ID", "CHATWOOT_INBOX_ID"):
            value = _value(environ, name)
            checks[f"positive.{name}"] = bool(re.fullmatch(r"[0-9]+", value)) and bool(value.strip("0"))
        assignee = _value(environ, "CHATWOOT_ASSIGNEE_ID")
        mode = _value(environ, "CHATWOOT_ASSIGNMENT_MODE") or ("fixed" if assignee else "automatic")
        checks["chatwoot_assignment_mode"] = mode in {"automatic", "fixed"}
        checks["chatwoot_assignee"] = (not assignee and mode != "fixed") or (
            bool(re.fullmatch(r"[0-9]+", assignee)) and bool(assignee.strip("0"))
        )
    elif chatwoot is False:
        if any(_value(environ, name) for name in environ if str(name).startswith("CHATWOOT_") and name != "CHATWOOT_ENABLED"):
            warnings.append("Remove unused Chatwoot configuration; the deployed code must support CHATWOOT_ENABLED=false.")
    worker_flag = _flag(environ, "RUN_WORKER_IN_WEB", True)
    checks["embedded_worker_topology"] = worker_flag is True
    if _value(environ, "RUN_WORKER_IN_WEB"):
        warnings.append("Leave RUN_WORKER_IN_WEB unset for the initial embedded-worker topology.")

    if checks["business_id_format"]:
        client = root / "src" / "clients" / business
        try:
            checks["system_instruction_nonempty"] = bool((client / "system_instruction.txt").read_text(encoding="utf-8").strip())
        except (OSError, UnicodeError):
            checks["system_instruction_nonempty"] = False
        expected = f"src/clients/{business}/system_instruction.txt"
        selected = _value(environ, "GITHUB_SI_PATH").lstrip("/")
        checks["github_si_path_matches_business"] = not selected or str(PurePosixPath(selected)) == expected
        policy_raw = _value(environ, "BUSINESS_POLICY_JSON")
        policy_present = bool(policy_raw) or (client / "business_policy.json").is_file()
        checks["follow_up_calendar_explicit"] = follow_ups is False
        try:
            if not policy_raw and policy_present:
                policy_raw = (client / "business_policy.json").read_text(encoding="utf-8")
            document = json.loads(policy_raw) if policy_present else {}
            checks["follow_up_calendar_explicit"] = follow_ups is False or (
                isinstance(document, dict) and {"timezone", "contact_hours", "operating_hours"} <= document.keys()
            )
            policy_env = dict(environ)
            # Override the helper's repository root with the document inspected here.
            policy_env["BUSINESS_POLICY_JSON"] = policy_raw or "{}"
            load_business_policy(business, policy_env)
            checks["effective_business_policy_valid"] = True
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, OverflowError):
            checks["effective_business_policy_valid"] = False
    else:
        checks["system_instruction_nonempty"] = False
        checks["github_si_path_matches_business"] = False
        checks["effective_business_policy_valid"] = False

    try:
        files = json.loads(_value(environ, "PRESAVED_FILES_JSON") or "[]")
        checks["presaved_files_array"] = isinstance(files, list) and all(isinstance(item, dict) for item in files)
    except (ValueError, TypeError):
        checks["presaved_files_array"] = False
    return {
        "ok": all(checks.values()), "offline_only": True, "external_services_verified": False,
        "configured": configured, "flags": {"chatwoot_enabled": chatwoot, "follow_ups_enabled": follow_ups},
        "checks": checks, "failed_checks": [name for name, passed in checks.items() if not passed],
        "warnings": warnings,
    }


def main() -> int:
    result = check_setup()
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
