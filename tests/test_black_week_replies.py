import ast
import asyncio
import hashlib
import hmac
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import BackgroundTasks, HTTPException

from black_week import (
    COLOMBIA, END, ORDER_BUTTON, ORDER_MESSAGE, SUBSCRIBE_BUTTON, UNSUBSCRIBE_BUTTON, VERSION, order_context,
)
from webhook_utils import resolve_whatsapp_sender, valid_whatsapp_sender, whatsapp_destination_matches

TANAKA = "1279018015292550"
VELVET = "1330184450175963"
CONTACT = "573000000001"
ACTIVE = datetime(2026, 11, 27, 10, tzinfo=COLOMBIA)


def run_webhook(monkeypatch, *, label, business="velvet", channel=VELVET, destination=VELVET,
                kind="button", context=True, signed=True):
    """Exercise the actual ingress function without loading bot clients or queues."""
    path = Path(__file__).parents[1] / "main.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    receive = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "receive_webhook")
    receive.decorator_list = []
    store = Mock()
    archive = Mock()
    dispatch = Mock()
    monkeypatch.setitem(sys.modules, "central_runtime_client", SimpleNamespace(migration_frozen=lambda: False))
    monkeypatch.setitem(sys.modules, "database", SimpleNamespace(
        promotion_question_available=lambda _: False,
        promotion_question_exists=lambda *_: False,
        store_promotion_answer=store,
        store_campaign_exposure=archive,
    ))
    namespace = {
        "Request": object, "BackgroundTasks": BackgroundTasks, "HTTPException": HTTPException,
        "config": SimpleNamespace(BUSINESS_ID=business, WA_PHONE_NUMBER_ID=channel, WA_APP_SECRET="synthetic",
                                  CRM_PROMOTION_CONSENT_ENABLED=True),
        "uses_crm": lambda _: True, "hmac": hmac, "hashlib": hashlib,
        "ControlError": RuntimeError, "WHATSAPP_MEDIA_TYPES": set(),
        "resolve_whatsapp_sender": resolve_whatsapp_sender, "valid_whatsapp_sender": valid_whatsapp_sender,
        "whatsapp_destination_matches": whatsapp_destination_matches,
        "parse_timestamp": lambda value: datetime.fromtimestamp(int(value), timezone.utc),
        "get_control_store": lambda: SimpleNamespace(get_control=lambda _: {"version": 7}),
        "get_or_create_customer_state": Mock(), "record_customer_activity": Mock(),
        "_dispatch_before_ack": dispatch, "process_claimed_whatsapp_event": object(),
        "queue_enabled": lambda: True,
    }
    exec(compile(ast.Module(body=[receive], type_ignores=[]), str(path), "exec"), namespace)
    incoming = {"from": CONTACT, "id": "wamid.incoming", "timestamp": str(int(ACTIVE.timestamp())), "type": kind}
    if kind == "button":
        incoming["button"] = {"text": label, "payload": label}
    else:
        incoming["text"] = {"body": label}
    if context:
        incoming["context"] = {"from": "573000000002", "id": "wamid.template"}
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": destination}, "messages": [incoming],
    }}]}]}
    raw = json.dumps(payload).encode()

    async def body():
        return raw

    async def json_payload():
        return payload

    signature = hmac.new(b"synthetic", raw, hashlib.sha256).hexdigest() if signed else "invalid"
    request = SimpleNamespace(body=body, json=json_payload, headers={"x-hub-signature-256": "sha256=" + signature})
    response = asyncio.run(namespace["receive_webhook"](request, BackgroundTasks()))
    return response, dispatch, store, archive


def test_order_keeps_scope_and_uses_the_existing_incoming_queue(monkeypatch):
    response, dispatch, store, archive = run_webhook(monkeypatch, label=ORDER_BUTTON)
    assert response["accepted"] == 1
    assert dispatch.call_args.args[2:5] == (CONTACT, "Cliente", ORDER_MESSAGE)
    assert dispatch.call_args.kwargs["business_id"] == "velvet"
    assert dispatch.call_args.kwargs["crm_control_version"] == 7
    store.assert_not_called()
    archive.assert_called_once_with(CONTACT, "wamid.template", ACTIVE, "template_button")


@pytest.mark.parametrize("label,status", [(SUBSCRIBE_BUTTON, "granted"), (UNSUBSCRIBE_BUTTON, "revoked")])
def test_preferences_record_the_signed_reply_without_dispatching_to_the_bot(monkeypatch, label, status):
    response, dispatch, store, archive = run_webhook(monkeypatch, label=label)
    assert response["accepted"] == 1
    dispatch.assert_not_called()
    store.assert_called_once_with(CONTACT, "wamid.incoming", ACTIVE, status, "wamid.template", label, version=VERSION)
    archive.assert_called_once_with(CONTACT, "wamid.template", ACTIVE, "template_button")


def test_preferences_require_signature_correct_destination_and_template_context(monkeypatch):
    with pytest.raises(HTTPException) as error:
        run_webhook(monkeypatch, label=SUBSCRIBE_BUTTON, signed=False)
    assert error.value.status_code == 401
    for settings in ({"destination": TANAKA}, {"context": False}, {"business": "tanaka"}):
        result, dispatch, store, archive = run_webhook(monkeypatch, label=SUBSCRIBE_BUTTON, **settings)
        assert not result["accepted"]
        dispatch.assert_not_called()
        store.assert_not_called()
        archive.assert_not_called()
    _, dispatch, store, archive = run_webhook(monkeypatch, label=SUBSCRIBE_BUTTON, kind="text")
    dispatch.assert_called_once()
    assert dispatch.call_args.kwargs["reply_to_message_id"] == "wamid.template"
    store.assert_not_called()
    archive.assert_not_called()


def test_prices_use_the_correct_business_and_expire_at_six_in_colombia():
    tanaka = order_context("tanaka", TANAKA, ORDER_MESSAGE, [], ACTIVE)
    velvet = order_context("velvet", VELVET, ORDER_MESSAGE, [], ACTIVE)
    assert all(value in tanaka for value in ("70.000", "120.000", "180.000"))
    assert all(value in velvet for value in ("24.000", "37.500"))
    assert "37.500" not in tanaka and "120.000" not in velvet
    assert order_context("memos", VELVET, ORDER_MESSAGE, [], ACTIVE) == ""
    expired = order_context("velvet", VELVET, "La de doce", [{"role": "user", "content": ORDER_MESSAGE}], END)
    assert "ya terminó" in expired and "37.500" not in expired


def test_free_text_gets_timed_offer_and_verified_history_disambiguates():
    from black_week import CAMPAIGN_SOURCE, PREVIEW

    plain = order_context("velvet", VELVET, "¿Y la de doce?", [], ACTIVE)
    assert "37.500" in plain and "No asumas" in plain
    system_log = {"role": "system", "metadata": {"source": CAMPAIGN_SOURCE, "campaign_version": VERSION,
                  "campaign_evidence": "reported_send", "provider_message_id": "wamid.template"}}
    linked = order_context("velvet", VELVET, "¿Y la de doce?", [system_log], PREVIEW, reply_to="wamid.template")
    assert "tiene evidencia" in linked and "empieza el 27" in linked
    expired = order_context("velvet", VELVET, "¿Y la de doce?", [], END)
    assert "resumen guardado" in expired and "precios vigentes" in expired and "37.500" not in expired
