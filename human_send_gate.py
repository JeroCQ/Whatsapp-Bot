"""Reserve each actual send in the bot's durable authority before touching Meta."""

import hashlib
import json
import uuid
from contextlib import contextmanager

import requests

from human_support import uses_crm
from observability import current_trace, update_trace
from provider_errors import ProviderError


@contextmanager
def automatic_send_gate(settings, store_factory, phone, payload):
    if not uses_crm(settings):
        yield
        return
    trace = current_trace()
    if trace.get("crm_reserved_attempt"):
        # Human endpoint already made the authoritative reservation. Automatic
        # callers never receive this marker except inside this context.
        yield
        return
    version = trace.get("crm_control_version")
    if version is None:
        raise ProviderError(
            "meta",
            "control_validation",
            409,
            message="missing_control_epoch",
            confirmed_not_sent=True,
        )
    attempt_id = "auto-" + uuid.uuid4().hex
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    store = store_factory()
    result = store.begin_send(
        phone, attempt_id, "auto", expected_version=int(version), request_digest=digest
    )
    if result.get("status") != "reserved":
        raise ProviderError(
            "meta",
            "control_validation",
            409,
            message="human_control_fenced_auto_send",
            confirmed_not_sent=True,
        )
    update_trace(crm_reserved_attempt=attempt_id)
    try:
        yield
    except Exception as error:
        confirmed = isinstance(error, requests.exceptions.ConnectTimeout) or (
            isinstance(error, ProviderError)
            and (
                error.confirmed_not_sent
                or (error.status is not None and 400 <= error.status < 500)
            )
        )
        store.finish_send(
            attempt_id,
            "failed" if confirmed else "unknown",
            detail=type(error).__name__,
        )
        raise
    else:
        provider_id = current_trace().get("provider_message_id") or ""
        store.finish_send(
            attempt_id,
            "accepted" if provider_id else "unknown",
            provider_message_id=provider_id,
        )
        if not provider_id:
            raise ProviderError(
                "meta", "send_text", None, message="provider_acceptance_id_missing"
            )
    finally:
        update_trace(crm_reserved_attempt="")
