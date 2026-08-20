import hashlib
import hmac
import json
import time

import pytest

from chatwoot_security import chatwoot_scope, normalize_chatwoot_root, verify_chatwoot_signature


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

