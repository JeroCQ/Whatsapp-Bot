import time

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from crm_service.api import create_app
from crm_service.models import Agent, AuditEvent
from crm_service.sync import Synchronizer
from tests.test_sync import FakeSource, data


def token(settings, **overrides):
    claims = {
        "iss": "chatty-catalog-compass",
        "aud": "whatsapp-crm",
        "profile": "tanaka-agent",
        "allowed_businesses": ["tanaka"],
        "iat": int(time.time()),
        "exp": int(time.time()) + 120,
        **overrides,
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


@pytest.fixture
def client(settings, sessions):
    Synchronizer(settings, sessions, lambda biz: FakeSource(data())).run()
    app = create_app(settings)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + token(settings)
        yield client
    app.state.engine.dispose()


def test_all_inbox_orders_by_actual_activity_across_buckets_and_paginates(client, settings, sessions):
    for identity, hour in ((2, "13"), (3, "14")):
        rows = data(paused=False, message_id=identity)
        for table, values in rows.items():
            for row in values:
                row["phone_number"] = f"57300{identity}"
                if "created_at" in row:
                    row["created_at"] = f"2026-11-01T{hour}:00:00+00:00"
        assert Synchronizer(settings, sessions, lambda _: FakeSource(rows)).run("tanaka") == {"tanaka": "ok"}
    result, cursor = [], None
    for _ in range(3):
        params = {"bucket": "all", "limit": 1}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/v1/inbox", params=params)
        assert response.status_code == 200
        page = response.json()
        result.extend(item["phone_number"] for item in page["items"])
        cursor = page["next_cursor"]
    assert result == ["573003", "573002", "573001"]
    client.post("/v1/conversations/tanaka/573001/read", json={})
    assert [item["phone_number"] for item in client.get("/v1/inbox?bucket=all").json()["items"]] == result


def test_legacy_human_active_is_folded_into_remaining_human_inbox(client, sessions):
    from crm_service.models import CRMConversation

    with sessions.begin() as session:
        row = session.get(CRMConversation, ("tanaka", "573001"))
        row.bucket = "human_active"
        row.classification = {**row.classification, "bucket": "human_active"}
    items = client.get("/v1/inbox?bucket=awaiting_human").json()["items"]
    assert len(items) == 1 and items[0]["bucket"] == "awaiting_human"


def test_health_ready_and_capabilities(client):
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    caps = client.get("/v1/capabilities").json()
    assert caps["manual_handoff_enabled"] is False and caps["send_messages"] is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "wrong"},
        {"iss": "wrong"},
        {"exp": 1},
        {"iat": int(time.time()) + 600},
        {"profile": ""},
        {"allowed_businesses": "tanaka"},
        {"exp": int(time.time()) + 10000},
    ],
)
def test_invalid_jwt_claims(client, settings, overrides):
    client.headers["Authorization"] = "Bearer " + token(settings, **overrides)
    assert client.get("/v1/inbox").status_code == 401


def test_invalid_signature_and_missing_token(client):
    client.headers["Authorization"] = "Bearer invalid.signature"
    assert client.get("/v1/inbox").status_code == 401
    client.headers.pop("Authorization")
    assert client.get("/v1/inbox").status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/v1/inbox?business_id=memos",
        "/v1/inbox?business_id=tanaka&business_id=velvet",
        "/v1/conversations/memos/573001",
    ],
)
def test_cross_tenant_rejected(client, path):
    assert client.get(path).status_code == 403


def test_mutations_reject_cross_tenant(client):
    base = "/v1/conversations/memos/573001"
    assert client.patch(base + "/crm", json={"version": 1, "stage": "qualified"}).status_code == 403
    assert client.post(base + "/notes", json={"body": "test"}).status_code == 403
    assert client.post(base + "/tasks", json={"title": "test"}).status_code == 403
    assert client.post(base + "/read", json={}).status_code == 403


def test_listing_scoped_and_read_does_not_change_bucket(client):
    items = client.get("/v1/inbox").json()["items"]
    assert len(items) == 1 and items[0]["business_id"] == "tanaka"
    assert items[0]["read"] is False
    assert client.post("/v1/conversations/tanaka/573001/read", json={}).status_code == 200
    item = client.get("/v1/inbox").json()["items"][0]
    assert item["read"] is True and item["bucket"] == "awaiting_human"
    client.post("/v1/conversations/tanaka/573001/read", json={"read": False})
    assert client.get("/v1/inbox").json()["items"][0]["read"] is False


def test_read_is_per_actor(client, settings):
    client.post("/v1/conversations/tanaka/573001/read", json={})
    client.headers["Authorization"] = "Bearer " + token(settings, profile="another-agent")
    assert client.get("/v1/inbox").json()["items"][0]["read"] is False


def test_crm_update_and_optimistic_conflict(client, sessions):
    url = "/v1/conversations/tanaka/573001/crm"
    response = client.patch(url, json={"version": 1, "stage": "qualified", "tags": ["vip"]})
    assert response.status_code == 200 and response.json()["version"] == 2
    assert client.patch(url, json={"version": 1, "stage": "bad"}).status_code == 409
    assert len(client.get("/v1/inbox?tag=vip&stage=qualified&priority=4").json()["items"]) == 1
    assert client.get("/v1/inbox?tag=none").json()["items"] == []
    with sessions() as session:
        assert session.scalar(select(AuditEvent)).details == {"fields": ["stage", "tags"]}


@pytest.mark.parametrize("field", ["is_paused", "handoff_reason", "current_state"])
def test_no_source_mutations_exposed(client, field):
    assert (
        client.patch("/v1/conversations/tanaka/573001/crm", json={"version": 1, field: True}).status_code
        == 422
    )


@pytest.mark.parametrize(
    "resource,body,edited",
    [
        ("notes", {"body": "nota privada"}, {"body": "editada"}),
        ("tasks", {"title": "llamar"}, {"title": "llamar mañana", "completed": True}),
    ],
)
def test_crud_notes_and_tasks(client, resource, body, edited):
    base = f"/v1/conversations/tanaka/573001/{resource}"
    response = client.post(base, json=body)
    assert response.status_code == 201
    item = response.json()["id"]
    assert client.get(base + "/" + item).status_code == 200
    assert len(client.get(base).json()) == 1
    assert client.patch(base + "/" + item, json=edited).status_code == 200
    assert client.get(f"/v1/conversations/memos/573001/{resource}/{item}").status_code == 403
    assert client.delete(base + "/" + item).status_code == 204
    assert client.get(base + "/" + item).status_code == 404


def test_search_name_phone_and_content(client):
    for search in ("Cliente", "573001", "Hola"):
        assert len(client.get("/v1/inbox", params={"search": search}).json()["items"]) == 1
    assert client.get("/v1/inbox?search=missing").json()["items"] == []


def test_cursor_is_scoped_and_tamper_proof(client, settings):
    client.headers["Authorization"] = "Bearer " + token(
        settings, profile="admin", allowed_businesses=["tanaka", "memos", "velvet"]
    )
    first = client.get("/v1/inbox?limit=1").json()
    second = client.get("/v1/inbox", params={"limit": 1, "cursor": first["next_cursor"]}).json()
    assert first["items"][0]["business_id"] != second["items"][0]["business_id"]
    assert client.get("/v1/inbox", params={"cursor": first["next_cursor"] + "bad"}).status_code == 400
    assert (
        client.get("/v1/inbox", params={"bucket": "all", "cursor": first["next_cursor"]}).status_code == 400
    )
    client.headers["Authorization"] = "Bearer " + token(settings)
    assert client.get("/v1/inbox", params={"cursor": first["next_cursor"]}).status_code == 400


def test_deep_link_and_detail(client):
    response = client.get("/v1/conversations/tanaka/573001")
    assert response.status_code == 200
    result = response.json()
    assert result["chatwoot_url"] == "https://chatwoot-tanaka.example.com/app/accounts/1/conversations/123"
    assert len(result["messages"]) == 1


def test_pipeline_analytics_status_are_scoped(client):
    for route in ("pipeline", "analytics", "sync-status"):
        items = client.get("/v1/" + route).json()["items"]
        assert {i["business_id"] for i in items} == {"tanaka"}


def test_browser_rejected(client):
    assert client.get("/v1/inbox", headers={"Origin": "https://frontend.example.com"}).status_code == 403


def test_internal_endpoint_requires_separate_secret(client, settings, monkeypatch):
    monkeypatch.setattr(Synchronizer, "run", lambda *_: pytest.fail("API must not read sources"))
    body = {"business_id": "tanaka", "idempotency_key": "backfill-1"}
    assert client.post("/internal/sync", json=body).status_code == 401
    response = client.post("/internal/sync", json=body, headers={"x-crm-sync-secret": settings.sync_secret})
    assert response.status_code == 202 and response.json()["status"] == "pending"


def test_notification_preferences_scoped(client):
    assert client.patch("/v1/me/preferences", json={"business_id": "memos"}).status_code == 403
    assert (
        client.patch("/v1/me/preferences", json={"business_id": "tanaka", "in_app": False}).status_code == 200
    )
    assert client.get("/v1/me/preferences").json()["tanaka"]["in_app"] is False


def test_assignee_must_have_same_business_access(client, sessions):
    with sessions.begin() as session:
        session.add(Agent(profile="memos-agent", allowed_businesses=["memos"]))
    assert (
        client.patch(
            "/v1/conversations/tanaka/573001/crm", json={"version": 1, "assigned_to": "memos-agent"}
        ).status_code
        == 422
    )


def test_push_validation_dedup_and_ownership(client, settings):
    body = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/test",
        "keys": {"p256dh": "a" * 87, "auth": "b" * 22},
    }
    first = client.post("/v1/push/subscriptions", json=body)
    assert first.status_code == 201
    assert client.post("/v1/push/subscriptions", json=body).json()["id"] == first.json()["id"]
    assert (
        client.post("/v1/push/subscriptions", json={**body, "endpoint": "https://127.0.0.1/"}).status_code
        == 422
    )
    client.headers["Authorization"] = "Bearer " + token(settings, profile="another-agent")
    assert client.delete("/v1/push/subscriptions/" + first.json()["id"]).status_code == 404
    assert client.post("/v1/push/subscriptions", json=body).status_code == 409


def test_push_configuration_requires_current_server_authentication_and_scope(client, settings):
    assert client.get("/v1/push/config").json() == {
        "enabled": False,
        "configured": False,
        "public_key": None,
        "reason": "disabled",
        "allowed_businesses": ["tanaka"],
    }
    client.headers.pop("Authorization")
    assert client.get("/v1/push/config").status_code == 401
    client.headers["Authorization"] = "Bearer " + token(settings, allowed_businesses=["memos"])
    assert client.get("/v1/push/config").json()["allowed_businesses"] == ["memos"]


def test_push_configuration_is_usable_when_enabled(notifications_settings, sessions):
    app = create_app(notifications_settings)
    try:
        with TestClient(app) as client:
            client.headers["Authorization"] = "Bearer " + token(notifications_settings)
            config = client.get("/v1/push/config").json()
            assert config["configured"] and config["public_key"]
            assert notifications_settings.vapid_private_key not in str(config)
    finally:
        app.state.engine.dispose()


def test_partial_task_patch(client):
    base = "/v1/conversations/tanaka/573001/tasks"
    item = client.post(base, json={"title": "Pendiente"}).json()["id"]
    response = client.patch(base + "/" + item, json={"completed": True})
    assert response.status_code == 200 and response.json()["completed"] is True
    assert client.patch(base + "/" + item, json={"title": None}).status_code == 422


def test_wrong_jwt_algorithm(client, settings):
    valid = jwt.decode(token(settings), options={"verify_signature": False})
    client.headers["Authorization"] = "Bearer " + jwt.encode(valid, settings.jwt_secret, algorithm="HS384")
    assert client.get("/v1/inbox").status_code == 401


def test_empty_authorized_scope_cannot_query_a_business(client, settings):
    client.headers["Authorization"] = "Bearer " + token(settings, allowed_businesses=["not_configured"])
    assert client.get("/v1/inbox").json()["items"] == []
    assert client.get("/v1/inbox?business_id=tanaka").status_code == 403
