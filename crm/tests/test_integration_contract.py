"""Executable frontend contract: local HTTP and disposable CRM PostgreSQL only."""

import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from crm_service.api import create_app
from crm_service.classification import classify
from crm_service.models import CRMConversation, SourceConversation, SourceMessage

CONVERSATION = "/v1/conversations/{business_id}/{phone_number}"
DOCUMENT = Path(__file__).parents[1] / "docs" / "openapi.json"
SUMMARY_KEYS = {
    "last_activity_at",
    "business_id",
    "phone_number",
    "name",
    "bucket",
    "attention_since",
    "waiting_seconds",
    "unanswered_customer_count",
    "priority",
    "priority_reason",
    "last_incoming_at",
    "last_message_id",
    "data_quality_warnings",
    "stage",
    "tags",
    "assigned_to",
    "version",
    "read",
    "chatwoot_url",
}
OPERATIONS = {
    ("GET", "/health"),
    ("GET", "/ready"),
    ("GET", "/v1/capabilities"),
    ("GET", "/v1/businesses"),
    ("GET", "/v1/agents"),
    ("GET", "/v1/inbox"),
    ("GET", CONVERSATION),
    ("GET", CONVERSATION + "/control"),
    ("GET", CONVERSATION + "/commands/{command_id}"),
    ("GET", CONVERSATION + "/message-updates"),
    ("POST", CONVERSATION + "/attachments"),
    ("GET", CONVERSATION + "/attachments/{asset_id}"),
    ("GET", CONVERSATION + "/attachments/{asset_id}/content"),
    ("POST", CONVERSATION + "/take"),
    ("POST", CONVERSATION + "/messages"),
    ("POST", CONVERSATION + "/close"),
    ("PATCH", CONVERSATION + "/crm"),
    ("POST", CONVERSATION + "/read"),
    ("GET", CONVERSATION + "/notes"),
    ("POST", CONVERSATION + "/notes"),
    ("GET", CONVERSATION + "/notes/{item_id}"),
    ("PATCH", CONVERSATION + "/notes/{item_id}"),
    ("DELETE", CONVERSATION + "/notes/{item_id}"),
    ("GET", CONVERSATION + "/tasks"),
    ("POST", CONVERSATION + "/tasks"),
    ("GET", CONVERSATION + "/tasks/{item_id}"),
    ("PATCH", CONVERSATION + "/tasks/{item_id}"),
    ("DELETE", CONVERSATION + "/tasks/{item_id}"),
    ("GET", "/v1/pipeline"),
    ("GET", "/v1/analytics"),
    ("GET", "/v1/notifications"),
    ("GET", "/v1/me/preferences"),
    ("PATCH", "/v1/me/preferences"),
    ("POST", "/v1/push/subscriptions"),
    ("DELETE", "/v1/push/subscriptions/{item_id}"),
    ("GET", "/v1/sync-status"),
    ("POST", "/internal/sync"),
    ("GET", "/internal/sync/{request_id}"),
}


def signed_headers(settings, **overrides):
    issued_at = int(time.time())
    claims = {
        "iss": "chatty-catalog-compass",
        "aud": "whatsapp-crm",
        "profile": "employee-007",
        "allowed_businesses": ["tanaka", "memos"],
        "iat": issued_at,
        "exp": issued_at + 120,
        **overrides,
    }
    return {"Authorization": "Bearer " + jwt.encode(claims, settings.jwt_secret, algorithm="HS256")}


@pytest.fixture
def contract_client(settings, sessions):
    at = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    messages = [
        {
            "id": 1,
            "role": "user",
            "content": "Consulta sintética",
            "created_at": at,
            "metadata": {"source_event_id": "fixture-inbound-1"},
        },
        {
            "id": 2,
            "role": "user",
            "content": "Segunda consulta sintética",
            "created_at": at,
            "metadata": {"source_event_id": "fixture-inbound-2"},
        },
    ]
    state = {"is_paused": True, "chatwoot_conversation_id": 123, "last_interaction": at.isoformat()}
    with sessions.begin() as session:
        for business in ("tanaka", "memos"):
            session.add(
                SourceConversation(
                    business_id=business,
                    phone_number="573000000001",
                    name="Contacto sintético",
                    state=state,
                    customer={"phone_number": "573000000001"},
                )
            )
            session.flush()
            for message in messages:
                session.add(
                    SourceMessage(
                        business_id=business,
                        source_id=message["id"],
                        phone_number="573000000001",
                        role=message["role"],
                        content=message["content"],
                        created_at=at,
                        message_metadata=message["metadata"],
                        event_key=f"fixture-{message['id']}",
                    )
                )
            classification = classify(state, messages, {}, at)
            session.add(
                CRMConversation(
                    business_id=business,
                    phone_number="573000000001",
                    bucket=classification["bucket"],
                    classification=classification,
                    sort_key=classification["sort_key"],
                )
            )
    api_settings = replace(
        settings,
        businesses={
            name: replace(business, source_url="", source_key="", readonly_database_url="")
            for name, business in settings.businesses.items()
        },
    )
    app = create_app(api_settings)
    with TestClient(app) as client:
        client.headers.update(signed_headers(settings))
        yield client
    app.state.engine.dispose()


def test_openapi_matches_published_contract(contract_client):
    actual = contract_client.get("/openapi.json").json()
    assert actual == json.loads(DOCUMENT.read_text())
    assert {
        (method.upper(), path) for path, operations in actual["paths"].items() for method in operations
    } == OPERATIONS


def test_inbox_contract_filters_and_cursor(contract_client):
    response = contract_client.get(
        "/v1/inbox",
        params=[
            ("business_id", "tanaka"),
            ("business_id", "memos"),
            ("bucket", "awaiting_human"),
            ("priority", "4"),
            ("assigned_to", "unassigned"),
            ("stage", "new"),
            ("search", "sintética"),
            ("limit", "1"),
        ],
    )
    assert response.status_code == 200
    page = response.json()
    assert set(page) == {"items", "next_cursor"} and isinstance(page["next_cursor"], str)
    first = page["items"][0]
    assert set(first) == SUMMARY_KEYS
    assert first["bucket"] == "awaiting_human" and first["version"] == 1 and first["read"] is False
    assert isinstance(first["waiting_seconds"], int) and first["waiting_seconds"] >= 0
    assert first["unanswered_customer_count"] == 2 and first["priority"] == 4
    assert first["assigned_to"] is None and first["tags"] == []
    assert first["chatwoot_url"].endswith("/app/accounts/1/conversations/123")
    params = list(response.request.url.params.multi_items()) + [("cursor", page["next_cursor"])]
    other = contract_client.get("/v1/inbox", params=params).json()
    assert other["next_cursor"] is None
    assert other["items"][0]["business_id"] != first["business_id"]
    assert other["items"][0]["phone_number"] == first["phone_number"]


def test_detail_contract_message_pagination(contract_client):
    path = "/v1/conversations/tanaka/573000000001"
    first = contract_client.get(path, params={"limit": 1}).json()
    assert set(first) == SUMMARY_KEYS | {
        "source_state",
        "customer",
        "handoff_delivery",
        "messages",
        "next_cursor",
        "delivery_cursor",
    }
    assert isinstance(first["next_cursor"], str)
    assert len(first["messages"]) == 1
    message = first["messages"][0]
    assert set(message) == {
        "id",
        "role",
        "content",
        "created_at",
        "metadata",
        "event_key",
        "delivery_status",
        "provider_message_id",
        "command_id",
        "attachments",
        "is_delivery_event",
    }
    assert isinstance(first["delivery_cursor"], str)
    assert message["attachments"] == [] and message["is_delivery_event"] is False
    assert message["id"] == 2 and message["metadata"]["source_event_id"] == "fixture-inbound-2"
    older = contract_client.get(path, params={"limit": 1, "cursor": first["next_cursor"]}).json()
    assert older["next_cursor"] is None and older["messages"][0]["id"] == 1


def test_crm_notes_tasks_and_read_wire_contract(contract_client):
    path = "/v1/conversations/tanaka/573000000001"
    updated = contract_client.patch(
        path + "/crm",
        json={
            "version": 1,
            "stage": "qualified",
            "assigned_to": "employee-007",
            "tags": ["synthetic", "synthetic"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["tags"] == ["synthetic"] and updated.json()["version"] == 2
    conflict = contract_client.patch(path + "/crm", json={"version": 1, "stage": "stale"})
    assert conflict.status_code == 409 and isinstance(conflict.json()["detail"], str)
    assert contract_client.post(path + "/read", json={"read": True}).json() == {"read": True}
    read = contract_client.get(path).json()
    assert read["read"] and read["bucket"] == "awaiting_human"
    assert not contract_client.get("/v1/conversations/memos/573000000001").json()["read"]
    note = contract_client.post(path + "/notes", json={"body": "Nota ficticia"})
    assert note.status_code == 201
    assert set(note.json()) == {"id", "body", "profile", "updated_at"}
    assert note.json()["profile"] == "employee-007"
    task = contract_client.post(
        path + "/tasks",
        json={
            "title": "Revisar caso ficticio",
            "due_at": "2030-01-01T10:00:00Z",
            "assigned_to": "employee-007",
            "completed": False,
        },
    )
    assert task.status_code == 201
    assert set(task.json()) == {"id", "title", "profile", "assigned_to", "due_at", "completed"}
    for resource, created in (("notes", note), ("tasks", task)):
        item = path + "/" + resource + "/" + created.json()["id"]
        assert contract_client.get(item).json() == created.json()
        assert contract_client.delete(item).status_code == 204
        assert contract_client.get(item).status_code == 404


@pytest.mark.parametrize("header", ["Origin", "Sec-Fetch-Site"])
def test_proxy_must_strip_browser_headers(contract_client, header):
    assert contract_client.get("/v1/inbox", headers={header: "same-origin"}).status_code == 403
    assert contract_client.get("/v1/inbox").status_code == 200


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "other"},
        {"iss": "other"},
        {"exp": 1},
        {"allowed_businesses": "tanaka"},
        {"profile": ""},
        {"iat": 1, "exp": 10000},
    ],
)
def test_required_jwt_contract(contract_client, settings, claims):
    result = contract_client.get("/v1/inbox", headers=signed_headers(settings, **claims))
    assert result.status_code == 401


def test_scope_is_not_selected_by_query(contract_client, settings):
    headers = signed_headers(settings, allowed_businesses=["tanaka"])
    denied = contract_client.get("/v1/inbox", params={"business_id": "memos"}, headers=headers)
    assert denied.status_code == 403
    assert contract_client.get("/v1/businesses", headers=headers).json() == [{"business_id": "tanaka"}]


@pytest.mark.parametrize(
    "path",
    [
        "/v1/tasks",
        "/v1/analytics/response-times",
        "/v1/push/config",
        "/v1/vapid-public-key",
        "/v1/agents/employee-007/revoke",
    ],
)
def test_unimplemented_endpoints_are_not_advertised_as_available(contract_client, path):
    assert contract_client.get(path).status_code in (404, 405)


def test_preference_and_snapshot_envelopes(contract_client):
    assert contract_client.patch(
        "/v1/me/preferences", json={"business_id": "tanaka", "in_app": False, "web_push": False}
    ).json() == {"ok": True}
    assert contract_client.get("/v1/me/preferences").json()["tanaka"] == {"in_app": False, "web_push": False}
    assert contract_client.get("/v1/notifications").json() == {"items": []}
    assert set(contract_client.get("/v1/analytics").json()) == {"snapshot_at", "items"}
    assert set(contract_client.get("/v1/pipeline").json()) == {"items"}
    assert set(contract_client.get("/v1/sync-status").json()) == {"items"}


def test_capabilities_do_not_claim_effective_production_permissions(contract_client):
    capabilities = contract_client.get("/v1/capabilities").json()
    assert capabilities["source_read_only"] is False
    assert capabilities["source_methods_read_only"] is True
    assert capabilities["source_credentials_verified"] is False
    assert capabilities["production_sources_enabled"] is False
    assert capabilities["source_access_mode"] == "synthetic"
    assert capabilities["sync_execution"] == "worker"
    assert capabilities["notifications_enabled"] is False
    assert not any(
        capabilities[name]
        for name in ("manual_handoff_enabled", "send_messages", "resolve_conversations", "resume_bot")
    )


def test_internal_sync_is_a_worker_request_without_source_credentials(contract_client, settings):
    assert all(
        not business.source_key and not business.readonly_database_url
        for business in contract_client.app.state.settings.businesses.values()
    )
    body = {"business_id": "tanaka", "idempotency_key": "synthetic-contract-run"}
    assert contract_client.post("/internal/sync", json=body).status_code == 401
    headers = {"X-CRM-Sync-Secret": settings.sync_secret}
    first = contract_client.post("/internal/sync", headers=headers, json=body)
    assert first.status_code == 202
    result = first.json()
    assert set(result) == {"id", "business_id", "status", "created_at", "updated_at", "result"}
    assert result["business_id"] == "tanaka" and result["status"] == "pending"
    repeated = contract_client.post("/internal/sync", headers=headers, json=body)
    assert repeated.status_code == 202 and repeated.json()["id"] == result["id"]
    state = contract_client.get("/internal/sync/" + result["id"], headers=headers)
    assert state.status_code == 200 and state.json() == result
    assert (
        contract_client.get(
            "/internal/sync/00000000-0000-0000-0000-000000000000", headers=headers
        ).status_code
        == 404
    )
    other = contract_client.post("/internal/sync", headers=headers, json={**body, "business_id": "memos"})
    assert other.status_code == 202 and other.json()["id"] != result["id"]
    assert (
        contract_client.post(
            "/internal/sync", headers=headers, json={**body, "business_id": "unknown"}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("header", ["Origin", "Sec-Fetch-Site"])
def test_internal_endpoint_also_rejects_browser_headers(contract_client, settings, header):
    response = contract_client.post(
        "/internal/sync",
        json={"business_id": "tanaka", "idempotency_key": "browser-must-not-enqueue"},
        headers={"X-CRM-Sync-Secret": settings.sync_secret, header: "same-origin"},
    )
    assert response.status_code == 401
