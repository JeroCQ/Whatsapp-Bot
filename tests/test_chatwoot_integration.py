import hashlib
import hmac
import json
import os
import sys
import types

import pytest
from fastapi.testclient import TestClient

os.environ["BUSINESS_ID"] = "client_1"
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("WA_VERIFY_TOKEN", "verify")
os.environ.setdefault("WA_TOKEN", "wa-secret-token")
os.environ.setdefault("WA_PHONE_NUMBER_ID", "phone-id")
os.environ.setdefault("GEMINI_API_KEY", "gemini")
os.environ.setdefault("DASHBOARD_API_KEY", "dashboard-secret")
os.environ.setdefault("CHATWOOT_BASE_URL", "https://app.chatwoot.com")
os.environ.setdefault("CHATWOOT_API_TOKEN", "chatwoot-secret-token")
os.environ.setdefault("CHATWOOT_ACCOUNT_ID", "10")
os.environ.setdefault("CHATWOOT_INBOX_ID", "20")
os.environ.setdefault("CHATWOOT_WEBHOOK_SECRET", "webhook-secret")

fake_bot = types.ModuleType("bot")
fake_bot.FILE_CATALOG = {}
fake_bot.process_message_logic = lambda *args: None
fake_bot.transcribe_audio_message = lambda *args: None
sys.modules.setdefault("bot", fake_bot)

import main
import chatwoot_api
from provider_errors import ProviderError


def payload(account=10, inbox=20, event="message_created"):
    return {"event": event, "id": 91, "account": {"id": account}, "inbox": {"id": inbox},
            "conversation": {"id": 44, "inbox_id": inbox, "account": {"id": account}},
            "message_type": "outgoing", "private": False, "content": "agent reply"}


def signed(body: bytes, secret="webhook-secret"):
    timestamp = "2000000000"
    signature = "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"X-Chatwoot-Timestamp": timestamp, "X-Chatwoot-Signature": signature, "Content-Type": "application/json"}


def test_webhook_auth_scope_precedes_idempotency(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main.time, "time", lambda: 2000000000)
    claimed = []
    monkeypatch.setattr(main, "claim_webhook_event", lambda *args: claimed.append(args) or True)
    for headers, expected in [({}, 401), ({"X-Chatwoot-Timestamp": "x", "X-Chatwoot-Signature": "bad"}, 401)]:
        assert client.post("/chatwoot-webhook", content=json.dumps(payload()), headers=headers).status_code == expected
    for body_data in (payload(account=11), payload(inbox=21)):
        body = json.dumps(body_data).encode()
        assert client.post("/chatwoot-webhook", content=body, headers=signed(body)).status_code == 403
    assert claimed == []


def test_valid_webhook_and_root_dispatcher_constraint(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main.time, "time", lambda: 2000000000)
    monkeypatch.setattr(main, "claim_webhook_event", lambda *args: True)
    monkeypatch.setattr(main, "_dispatch", lambda *args, **kwargs: "queued")
    body = json.dumps(payload()).encode()
    assert client.post("/chatwoot-webhook", content=body, headers=signed(body)).status_code == 200
    assert client.post("/", json=payload()).status_code == 404


class Response:
    def __init__(self, status, body=None, headers=None, chunks=None):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}
        self._chunks = chunks or []
        self.content = b"".join(self._chunks)
    def json(self): return self._body
    def raise_for_status(self):
        if self.status_code >= 400: raise main.requests.HTTPError(response=self)
    def iter_content(self, size): return iter(self._chunks)
    def close(self): pass


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
def test_meta_failures_raise_and_redact(monkeypatch, capsys, status):
    response = Response(status, {"error": {"code": 190, "error_subcode": 7, "message": "bad wa-secret-token Authorization: Bearer leak"}})
    monkeypatch.setattr(main, "post", lambda *args, **kwargs: response)
    with pytest.raises(ProviderError) as caught:
        main.send_whatsapp_message("57300", "hello")
    print(caught.value)
    output = capsys.readouterr().out
    assert "wa-secret-token" not in output and "Bearer leak" not in output


def test_meta_200_returns_success(monkeypatch):
    monkeypatch.setattr(main, "post", lambda *args, **kwargs: Response(200))
    assert main.send_whatsapp_message("57300", "hello") is True


def test_failed_primary_response_suppresses_followup(monkeypatch):
    turn = type("Turn", (), {"send_files_before_response": False, "requested_files": [], "response": "reply",
                              "follow_up_message": "later", "follow_up_delay_minutes": 10})()
    monkeypatch.setattr(main, "invalidate_follow_up", lambda *a: None)
    monkeypatch.setattr(main, "get_or_create_customer_state", lambda *a: {"is_paused": False, "chatwoot_conversation_id": None})
    monkeypatch.setattr(main, "process_message_logic", lambda *a: turn)
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *a: (_ for _ in ()).throw(ProviderError("meta", "send", 400)))
    scheduled = []
    monkeypatch.setattr(main, "schedule_follow_up", lambda *a: scheduled.append(a))
    with pytest.raises(ProviderError):
        main._process_whatsapp_message_unlocked("57300", "Name", "hello")
    assert scheduled == []


def test_agent_text_forwarding_is_scoped(monkeypatch):
    sent = []
    monkeypatch.setattr(main, "get_phone_by_chatwoot_id", lambda value: "57300")
    monkeypatch.setattr(main, "save_message_log", lambda *a: None)
    monkeypatch.setattr(main, "send_whatsapp_message", lambda *a: sent.append(a) or True)
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *a, **k: None)
    main.process_chatwoot_event(payload())
    assert sent == [("57300", "agent reply")]
    sent.clear()
    main.process_chatwoot_event(payload(account=99))
    assert sent == []


def test_agent_attachment_forwarding(monkeypatch):
    event = payload()
    event["attachments"] = [{"data_url": "/rails/active_storage/file.png", "content_type": "image/png", "file_name": "proof.png"}]
    sent = []
    monkeypatch.setattr(main, "get_phone_by_chatwoot_id", lambda value: "57300")
    monkeypatch.setattr(main, "save_message_log", lambda *a: None)
    monkeypatch.setattr(main, "upload_chatwoot_attachment_to_meta", lambda *a: "media-1")
    monkeypatch.setattr(main, "send_whatsapp_media", lambda *a: sent.append(a) or True)
    monkeypatch.setattr(main, "mark_webhook_event_processed", lambda *a, **k: None)
    main.process_chatwoot_event(event)
    assert sent == [("57300", "media-1", "image", "agent reply", "proof.png")]


def test_chatwoot_provider_log_redacts_api_token(monkeypatch, capsys):
    response = Response(401, {"error": {"message": "bad chatwoot-secret-token api_access_token=leak"}})
    monkeypatch.setattr(chatwoot_api, "post", lambda *a, **k: response)
    assert chatwoot_api.create_conversation(77) is None
    output = capsys.readouterr().out
    assert "chatwoot-secret-token" not in output and "api_access_token=leak" not in output


def test_relative_attachment_streams_and_cross_origin_is_rejected(monkeypatch):
    downloads = []
    def fake_get(url, **kwargs):
        downloads.append((url, kwargs.get("headers")))
        return Response(200, headers={"Content-Type": "image/png", "Content-Length": "3"}, chunks=[b"abc"])
    monkeypatch.setattr(main, "get", fake_get)
    monkeypatch.setattr(main, "post", lambda *a, **k: Response(200, {"id": "media-1"}))
    assert main.upload_chatwoot_attachment_to_meta("/rails/active_storage/file.png") == "media-1"
    assert downloads[0][0].startswith("https://app.chatwoot.com/")
    assert main.upload_chatwoot_attachment_to_meta("https://evil.example/file") is None
    assert len(downloads) == 1


def test_cross_origin_redirect_gets_no_second_credentialed_request(monkeypatch):
    calls = []
    def fake_get(url, **kwargs):
        calls.append((url, kwargs.get("headers")))
        return Response(302, headers={"Location": "https://evil.example/file"})
    monkeypatch.setattr(main, "get", fake_get)
    assert main.upload_chatwoot_attachment_to_meta("/file") is None
    assert len(calls) == 1


def test_attachment_size_limit(monkeypatch):
    monkeypatch.setattr(main.config, "CHATWOOT_MAX_ATTACHMENT_BYTES", 2)
    monkeypatch.setattr(main, "get", lambda *a, **k: Response(200, headers={"Content-Length": "3"}, chunks=[b"abc"]))
    assert main.upload_chatwoot_attachment_to_meta("/file") is None


def test_account_path_and_configured_inbox_creation(monkeypatch):
    captured = {}
    def fake_post(url, **kwargs):
        captured.update(url=url, json=kwargs["json"])
        return Response(200, {"id": 88})
    monkeypatch.setattr(chatwoot_api, "post", fake_post)
    assert chatwoot_api.create_conversation(77) == 88
    assert captured["url"] == "https://app.chatwoot.com/api/v1/accounts/10/conversations"
    assert captured["json"]["inbox_id"] == 20
