import hashlib
import hmac
import json
import time

import pytest

from chatwoot_security import chatwoot_scope, normalize_chatwoot_root, verify_chatwoot_signature, chatwoot_event_diagnostics


@pytest.mark.parametrize("root", [
    "https://app.chatwoot.com",
    "https://app.chatwoot.com/",
    "https://chatwoot-production-xxxx.up.railway.app",
])
def test_cloud_and_self_hosted_roots(root):
    assert normalize_chatwoot_root(root).startswith("https://")
    assert not normalize_chatwoot_root(root).endswith("/")


@pytest.mark.parametrize("root", [
    "http://app.chatwoot.com", "https://user:pass@app.chatwoot.com",
    "https://app.chatwoot.com/api/v1", "https://app.chatwoot.com/app",
    "https://app.chatwoot.com?secret=x", "https://app.chatwoot.com/#fragment",
])
def test_invalid_chatwoot_roots(root):
    with pytest.raises(ValueError):
        normalize_chatwoot_root(root)


def test_native_v4162_signature_fixture_and_timestamp():
    body = json.dumps({"event": "message_created"}, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    signature = "sha256=" + hmac.new(b"deployment-secret", timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    assert verify_chatwoot_signature(body, timestamp, signature, "deployment-secret")
    assert not verify_chatwoot_signature(body, timestamp, signature, "wrong-secret")
    assert not verify_chatwoot_signature(body, "1", signature, "deployment-secret")


def test_actual_message_and_conversation_scope_shapes():
    assert chatwoot_scope({"account": {"id": 10}, "inbox": {"id": 20}, "conversation": {"id": 3}}) == ("10", "20")
    assert chatwoot_scope({"account": {"id": 10}, "inbox_id": 20, "id": 3}) == ("10", "20")


def test_routing_diagnostics_drop_arbitrary_values_and_secrets():
    details = chatwoot_event_diagnostics({
        "event": "SECRET_EVENT", "status": "SECRET_STATUS", "id": "SECRET_ID",
        "account": {"id": "SECRET_ACCOUNT"}, "inbox_id": {"secret": "SECRET_INBOX"},
        "meta": {"assignee": {"id": "SECRET_ASSIGNEE", "email": "SECRET_EMAIL"}},
        "content": "SECRET_CONTENT", "api_access_token": "SECRET_TOKEN",
    })
    assert "SECRET_" not in json.dumps(details)
    assert details["conversation_id"] is None
    assert details["chatwoot_event"] == details["conversation_status"] == "unknown"


def test_v4162_conversation_event_exposes_status_and_assignment_without_message_body():
    # Conversations::EventDataPresenter#webhook_data adds account to push_data.
    event = {"event": "conversation_updated", "id": 433, "account": {"id": 2},
             "inbox_id": 1, "status": "snoozed", "meta": {"assignee": {"id": 3}},
             "messages": [{"content": "PRIVATE_CONTENT"}]}
    assert chatwoot_scope(event) == ("2", "1")
    details = chatwoot_event_diagnostics(event)
    assert details["conversation_id"] == "433"
    assert details["assignee_id"] == "3"
    assert details["conversation_status"] == "snoozed"
    assert "PRIVATE_CONTENT" not in json.dumps(details)

