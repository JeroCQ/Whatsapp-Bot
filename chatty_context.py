"""Business context bridge on the existing, verified Briosos consumer only.

No dynamic environment/client switching, second queue consumer or second sender.
Other businesses and numbers never contact the registry or telemetry sink.
"""
import hashlib
import json
import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from types import MappingProxyType
from urllib.parse import urlsplit

import requests

from chatty_usage import UsageCollector

BUSINESS = "briosos"
PHONE = "573105338124"
logger = logging.getLogger(__name__)
_context = ContextVar("chatty_business_context", default=None)
_collectors = {}
_collectors_lock = Lock()


class PilotCollector(UsageCollector):
    def _submit(self, kind, payload):
        context = current_context()
        if context is not None:
            super()._submit(kind, {"publication_version": context.publication_version,
                                  "channel_id": context.channel_id, "record": payload})


class PilotContextError(RuntimeError):
    pass


def enabled():
    return os.environ.get("CHATTY_PILOT_BRIDGE_ENABLED", "false").lower() == "true"


def storage_fingerprint(config):
    # Nonsecret identity, never credentials. The same adapter survives activation/reversal.
    return hashlib.sha256(f"{config.BUSINESS_ID}:{str(config.SUPABASE_URL).rstrip('/')}".encode()).hexdigest()


def registry_origin():
    value = os.environ.get("CHATTY_PILOT_REGISTRY_URL", "").rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise PilotContextError("pilot_registry_origin_invalid")
    return value


def registry_request(config, method, path, *, params=None, body=None):
    secret = str(getattr(config, "CRM_BOT_CONTROL_SECRET", ""))
    if len(secret) < 32:
        raise PilotContextError("pilot_server_secret_unavailable")
    try:
        with requests.request(method, registry_origin() + path, params=params, json=body,
                              headers={"X-CRM-Bot-Secret": secret}, timeout=(2, 3),
                              allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise PilotContextError("pilot_registry_rejected")
            data = bytearray()
            for chunk in response.iter_content(8192):
                data.extend(chunk)
                if len(data) > 65536:
                    raise PilotContextError("pilot_registry_response_too_large")
            value = json.loads(data)
            if not isinstance(value, dict):
                raise PilotContextError("pilot_registry_response_invalid")
            return value
    except (requests.RequestException, ValueError):
        raise PilotContextError("pilot_registry_unavailable") from None


@dataclass(frozen=True)
class LegacyStorageAdapter:
    business_id: str
    fingerprint: str
    client: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class BusinessContext:
    business_id: str
    phone_number: str
    channel_id: str
    publication_version: int
    revision: int
    mode: str
    configuration: object
    connection_references: object = field(repr=False)
    operators: tuple
    storage: LegacyStorageAdapter = field(repr=False)
    turn_id: str
    collector: object = field(repr=False, compare=False)

    def assert_engine(self, config, phone):
        if (self.business_id != config.BUSINESS_ID or self.phone_number != phone
                or self.channel_id != str(config.WA_PHONE_NUMBER_ID)
                or self.storage.fingerprint != storage_fingerprint(config)):
            raise PilotContextError("pilot_engine_storage_mismatch")


def resolve_context(config, business_id, channel_id, phone, event_id, client):
    if not enabled() or business_id != BUSINESS or phone != PHONE:
        return None
    if business_id != config.BUSINESS_ID or str(channel_id) != str(config.WA_PHONE_NUMBER_ID) or not event_id:
        raise PilotContextError("pilot_verified_identity_required")
    value = registry_request(config, "GET", "/internal/pilot/config",
                             params={"channel_id": str(channel_id), "phone_number": phone})
    required_refs = {key: f"legacy_settings:{business_id}:{key}" for key in ("bot_control", "source_read")}
    if (value.get("business_id") != business_id or value.get("phone_number") != phone
            or value.get("channel_id") != str(channel_id) or value.get("mode") not in {"shared", "legacy"}
            or value.get("storage_fingerprint") != storage_fingerprint(config)
            or any(value.get("connections", {}).get(key) != ref for key, ref in required_refs.items())
            or type(value.get("publication_version")) is not int or type(value.get("revision")) is not int
            or not isinstance(value.get("configuration"), dict) or not value.get("operators")):
        raise PilotContextError("pilot_configuration_scope_invalid")
    version = value["publication_version"]
    collector = None
    if value["mode"] == "shared":
        with _collectors_lock:
            collector = _collectors.get("pilot")
            if collector is None:
                def emit(kind, envelope):
                    registry_request(config, "POST", f"/internal/pilot/usage/{kind}", body=envelope)
                collector = PilotCollector(emit, enabled=True)
                _collectors["pilot"] = collector
    context = BusinessContext(business_id, phone, str(channel_id), version, value["revision"], value["mode"],
        MappingProxyType(value["configuration"]), MappingProxyType(value["connections"]),
        tuple(MappingProxyType(item) for item in value["operators"]),
        LegacyStorageAdapter(business_id, value["storage_fingerprint"], client),
        hashlib.sha256(f"{business_id}:{phone}:{event_id}".encode()).hexdigest(), collector)
    context.assert_engine(config, phone)
    return context


def current_context():
    return _context.get()


@contextmanager
def processing_scope(context):
    token = _context.set(context)
    try:
        yield context
    finally:
        _context.reset(token)


def reply_accepted():
    context = current_context()
    if context and context.collector:
        context.collector.record_turn(business_id=context.business_id, conversation_id=context.phone_number,
                                      turn_id=context.turn_id, outcome="reply_accepted")


def collector_status():
    with _collectors_lock:
        return [collector.status() for collector in _collectors.values()]


class ScopedStorage:
    """Keep the legacy adapter by default; requests cannot select arbitrary credentials."""
    def __init__(self, client):
        self.legacy = client

    def __getattr__(self, name):
        context = current_context()
        client = context.storage.client if context else self.legacy
        return getattr(client, name)
