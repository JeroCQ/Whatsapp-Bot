"""Opt-in startup configuration for the existing single-business bot process.

Absent CHATTY_RUNTIME_URL retains the deployed behavior byte-for-byte. This is
not a sender, worker or message-time registry lookup. Redis and Meta bindings
remain those of the existing business deployment.
"""
import json
import os
import re
from urllib.parse import urlsplit

import requests

_METADATA = {}
_EXACT = {
    "BUSINESS_ID", "APP_ENV", "SUPABASE_URL", "SUPABASE_KEY",
    "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SCHEMA", "SUPABASE_TABLE_PREFIX", "PRESAVED_FILES_JSON",
    "CATALOG_STORAGE_BUCKET", "PHONE_LOCK_TTL_SECONDS",
}
_PREFIXES = ("WA_", "GEMINI_", "CHATWOOT_", "DASHBOARD_", "GITHUB_", "CRM_")
_NEVER = {"CRM_DATABASE_URL", "CRM_JWT_SECRET", "CRM_SYNC_SECRET"}


def bootstrap_environment():
    origin = os.environ.get("CHATTY_RUNTIME_URL", "").rstrip("/")
    if not origin:
        return
    parsed = urlsplit(origin)
    business = os.environ.get("BUSINESS_ID", "")
    token = os.environ.get("CHATTY_RUNTIME_TOKEN", "")
    if (parsed.scheme != "https" or not parsed.hostname or parsed.path
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", business) or len(token) < 32):
        raise RuntimeError("central_runtime_bootstrap_configuration_invalid")
    snapshot = {key: value for key, value in os.environ.items()
                if key not in _NEVER and (key in _EXACT or key.startswith(_PREFIXES))}
    payload = {"configuration": snapshot,
               "commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", ""),
               "bot_api_url": "https://" + os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")}
    try:
        with requests.post(origin + f"/internal/runtime/businesses/{business}/bootstrap",
                           json=payload, headers={"X-Chatty-Runtime-Token": token},
                           timeout=(5, 20), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise RuntimeError("central_runtime_bootstrap_rejected")
            raw = bytearray()
            for block in response.iter_content(8192):
                raw.extend(block)
                if len(raw) > 262144:
                    raise RuntimeError("central_runtime_bootstrap_response_too_large")
            data = json.loads(raw)
        if (data.get("business_id") != business or data.get("mode") not in {"legacy", "central"}
                or str(data.get("channel_id")) != str(snapshot.get("WA_PHONE_NUMBER_ID"))):
            raise RuntimeError("central_runtime_bootstrap_scope_mismatch")
        overrides = data.get("environment", {})
        if not isinstance(overrides, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                  for k, v in overrides.items()):
            raise RuntimeError("central_runtime_bootstrap_environment_invalid")
        permitted = _EXACT | set(snapshot)
        if any(key not in permitted or key in _NEVER for key in overrides):
            raise RuntimeError("central_runtime_bootstrap_environment_out_of_scope")
        if data["mode"] == "central":
            if (overrides.get("BUSINESS_ID") != business
                    or overrides.get("WA_PHONE_NUMBER_ID") != snapshot.get("WA_PHONE_NUMBER_ID")):
                raise RuntimeError("central_runtime_channel_change_forbidden")
            os.environ.update(overrides)
        _METADATA.update({key: data.get(key) for key in
                          ("business_id", "channel_id", "version", "mode")})
    except (requests.RequestException, ValueError, KeyError, TypeError):
        # No credential-bearing response bodies and no silent legacy fallback
        # after central activation: an unavailable registry must not split writes.
        raise RuntimeError("central_runtime_bootstrap_unavailable") from None


def metadata():
    return dict(_METADATA)


def migration_frozen():
    if not os.environ.get("CHATTY_RUNTIME_URL"):
        return False
    from queue_client import get_queue
    queue = get_queue()
    if queue is None:
        raise RuntimeError("central_runtime_requires_existing_durable_queue")
    return bool(queue.connection.get("chatty:central:migration:frozen"))
