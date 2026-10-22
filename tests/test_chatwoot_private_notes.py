"""Internal notes must not be represented as incoming customer activity."""

import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import patch

import pytest


@pytest.fixture
def note_api(monkeypatch):
    private_config = types.ModuleType("config")
    private_config.config = types.SimpleNamespace(
        CHATWOOT_BASE_URL="https://chatwoot.example",
        CHATWOOT_ACCOUNT_ID="10",
        CHATWOOT_API_TOKEN="test-chatwoot-token",
        WA_TOKEN="test-wa-token",
    )
    path = Path(__file__).resolve().parents[1] / "chatwoot_api.py"
    spec = importlib.util.spec_from_file_location("_chatwoot_private_notes_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"config": private_config}):
        spec.loader.exec_module(module)

    calls = []
    response = types.SimpleNamespace(status_code=201, raise_for_status=lambda: None)

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return response

    def unexpected_operation(*args, **kwargs):
        pytest.fail("Sending a note must not look up contacts or change conversation state or assignment")

    monkeypatch.setattr(module, "post", post)
    for name in ("get", "put", "create_conversation", "get_or_create_contact"):
        monkeypatch.setattr(module, name, unexpected_operation)
    return module, calls, response


@pytest.mark.parametrize("is_private", [None, False, True], ids=["default_customer", "public_customer", "internal_note"])
def test_chatwoot_text_distinguishes_customer_messages_from_private_notes(note_api, is_private):
    api, calls, response = note_api
    kwargs = {} if is_private is None else {"is_private": is_private}

    assert api.send_message_to_chatwoot(44, "Texto de prueba", **kwargs) is response

    private = is_private is True
    assert len(calls) == 1
    url, request = calls[0]
    assert url == "https://chatwoot.example/api/v1/accounts/10/conversations/44/messages"
    assert request["json"] == {
        "content": "Texto de prueba",
        "message_type": "outgoing" if private else "incoming",
        "private": private,
    }
    assert request["allow_redirects"] is False
    assert request["headers"]["Content-Type"] == "application/json"


@pytest.mark.parametrize("is_private", [None, False, True], ids=["default_customer", "public_customer", "internal_note"])
def test_chatwoot_media_distinguishes_customer_attachments_from_private_notes(note_api, is_private):
    api, calls, response = note_api
    kwargs = {} if is_private is None else {"is_private": is_private}

    assert api.send_media_to_chatwoot(
        44, "Adjunto de prueba", b"test-image", "image/png", "test.png", **kwargs,
    ) is response

    private = is_private is True
    assert len(calls) == 1
    url, request = calls[0]
    assert url == "https://chatwoot.example/api/v1/accounts/10/conversations/44/messages"
    assert request["data"] == {
        "content": "Adjunto de prueba",
        "message_type": "outgoing" if private else "incoming",
        "private": "true" if private else "false",
    }
    assert request["files"] == {"attachments[]": ("test.png", b"test-image", "image/png")}
    assert request["allow_redirects"] is False
    assert request["headers"] == {"api_access_token": "test-chatwoot-token"}
    assert request["timeout"] == api.MEDIA_TIMEOUT


@pytest.mark.parametrize("initial_status", ["resolved", "snoozed"])
@pytest.mark.parametrize("kind", ["text", "media"])
def test_delayed_internal_note_does_not_reopen_a_closed_ticket_but_customer_activity_does(
    note_api, monkeypatch, initial_status, kind,
):
    api, calls, response = note_api
    conversation = {"status": initial_status}
    original_post = api.post

    def post_with_chatwoot_reopen_callback(url, **kwargs):
        # Chatwoot v4.16.2 Message.reopen_conversation checks incoming? without
        # excluding private messages. This fake models only that API callback.
        # https://github.com/chatwoot/chatwoot/blob/v4.16.2/app/models/message.rb#L403
        message = kwargs.get("json", kwargs.get("data"))
        if message["message_type"] == "incoming" and conversation["status"] in {"resolved", "snoozed"}:
            conversation["status"] = "open"
        return original_post(url, **kwargs)

    monkeypatch.setattr(api, "post", post_with_chatwoot_reopen_callback)

    def send(private):
        if kind == "text":
            return api.send_message_to_chatwoot(44, "Mensaje de prueba", is_private=private)
        return api.send_media_to_chatwoot(
            44, "Adjunto de prueba", b"test-image", "image/png", "test.png", is_private=private,
        )

    assert send(True) is response
    assert conversation["status"] == initial_status

    assert send(False) is response
    assert conversation["status"] == "open"
    assert len(calls) == 2
    assert all(url.endswith("/conversations/44/messages") for url, _request in calls)
