"""Bounded prototype checks: shared scope, queue routing, atomic transfer and notification recipients."""
import ast
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from sqlalchemy import select
from sqlalchemy.engine import make_url

from crm_service.api import create_app
from crm_service.models import (
    CRMConversation,
    NotificationEvent,
    PushDelivery,
    PushSubscription,
    SourceMessage,
    now,
)
from crm_service.notifications import dispatch, notification_allowed
from crm_service.routing import customer_queue
from crm_service.settings import Business
from crm_service.sync import Synchronizer
from tests.test_api import token
from tests.test_sync import FakeSource, data

ROOT = Path(__file__).parents[2]
PHONE = "573001"
RETAIL, WHOLESALE = "operator:memos", "operator:memos_mayoristas"


@pytest.fixture
def bot_sql(database_url):
    url = make_url(database_url).set(drivername="postgresql")
    assert url.host in {"127.0.0.1", "localhost"} and url.database.startswith("crm_test")
    schema = "queue_test_" + uuid.uuid4().hex[:10]
    tree = ast.parse((ROOT / "pilot_tests/acceptance_pilot.py").read_text())
    source_ddl = next(ast.literal_eval(node.value) for node in tree.body
                      if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SOURCE_DDL" for t in node.targets))
    text = (ROOT / "supabase/bootstrap.sql").read_text()
    section = text.split("-- CRM CONTROL BEGIN", 1)[1].split("-- CRM CONTROL END", 1)[0]
    connection = psycopg.connect(url.render_as_string(hide_password=False), autocommit=True)
    connection.execute(f"CREATE SCHEMA {schema}")
    connection.execute(f"SET search_path TO {schema}, pg_catalog")
    connection.execute(source_ddl)
    connection.execute(section.replace("public.", schema + ".").replace("search_path=pg_catalog,public", f"search_path=pg_catalog,{schema}"))
    connection.execute("INSERT INTO customers(phone_number) VALUES (%s)", (PHONE,))
    connection.execute("INSERT INTO conversation_states(phone_number,is_paused,current_state,crm_control_phase,crm_control_owner,crm_control_version,follow_up_suppressed) VALUES (%s,true,'HUMAN_HANDOFF','human',%s,7,true)", (PHONE, RETAIL))
    connection.execute("INSERT INTO message_logs(phone_number,role,content,metadata) VALUES (%s,'user','Synthetic inbound',%s)",
                       (PHONE, Jsonb({"received_at": now().isoformat(), "source_event_id": "synthetic-inbound"})))

    def call(action, command="", actor="", version=None, payload=None, conn=None):
        return (conn or connection).execute(f"SELECT {schema}.crm_control_dispatch(%s,%s,%s,%s,%s,%s,%s)",
            ("memos", PHONE, action, command, actor, version, Jsonb(payload or {}))).fetchone()[0]

    try:
        yield call, connection, schema, url
    finally:
        connection.rollback()
        connection.execute(f"DROP SCHEMA {schema} CASCADE")
        connection.close()


def test_bot_transfer_is_atomic_paused_idempotent_and_serializes_against_sends(bot_sql):
    call, connection, schema, url = bot_sql
    before = connection.execute("SELECT is_paused,crm_control_owner,crm_control_version,customer_data FROM conversation_states").fetchone()
    incremental = (ROOT / "supabase/upgrade_crm_operator_transfer.sql").read_text().replace("public.", schema + ".").replace("search_path=pg_catalog,public", f"search_path=pg_catalog,{schema}")
    connection.execute(incremental)
    assert connection.execute("SELECT is_paused,crm_control_owner,crm_control_version,customer_data FROM conversation_states").fetchone() == before
    result = call("transfer", "transfer-one", RETAIL, 7, {"target_actor": WHOLESALE})
    assert result["status"] == "confirmed"
    assert result["control"]["owner"] == WHOLESALE and result["control"]["is_paused"] is True
    assert result["control"]["version"] == 8
    assert call("transfer", "transfer-one", RETAIL, 7, {"target_actor": WHOLESALE}) == result
    assert call("begin_send", "old-owner", RETAIL, 8, {"kind": "human", "text": "never sent"})["http_status"] == 409
    assert connection.execute("SELECT follow_up_suppressed FROM conversation_states WHERE phone_number=%s", (PHONE,)).fetchone()[0] is True

    def racing(action):
        with psycopg.connect(url.render_as_string(hide_password=False), autocommit=True) as conn:
            payload = {"target_actor": RETAIL} if action == "transfer" else {"kind": "human", "text": "Synthetic reserved only"}
            return action, call(action, "race-" + action, WHOLESALE, 8, payload, conn)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = dict(pool.map(racing, ["transfer", "begin_send"]))
    assert sum("error" not in result for result in outcomes.values()) == 1
    live = call("read")
    assert live["is_paused"] is True and live["phase"] == "human"
    if "error" not in outcomes["begin_send"]:
        blocked = call("transfer", "blocked-transfer", WHOLESALE, live["version"], {"target_actor": RETAIL})
        assert blocked["error"] == "unsettled_send_blocks_transfer"
    assert connection.execute("SELECT count(*) FROM message_logs WHERE content LIKE 'RESOLVED:%'").fetchone()[0] == 0


def test_incremental_transfer_function_matches_fresh_and_upgrade_installations():
    def function(path):
        source = (ROOT / "supabase" / path).read_text()
        return source.split("CREATE OR REPLACE FUNCTION public.crm_control_dispatch(", 1)[1].split("REVOKE ALL ON FUNCTION", 1)[0].strip()
    expected = function("bootstrap.sql")
    for path in ("upgrade_existing_brand.sql", "upgrade_crm_bot_commands.sql", "upgrade_crm_operator_transfer.sql"):
        assert function(path) == expected


def test_bot_transfer_endpoint_uses_existing_store_and_leaves_followups_untouched(bot_sql, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    for key, value in {"BUSINESS_ID": "memos", "SUPABASE_URL": "https://synthetic.invalid",
                       "SUPABASE_KEY": "synthetic-key", "WA_VERIFY_TOKEN": "synthetic-verify",
                       "WA_TOKEN": "synthetic-meta", "WA_PHONE_NUMBER_ID": "synthetic-phone",
                       "GEMINI_API_KEY": "synthetic-gemini"}.items():
        monkeypatch.setenv(key, value)
    from fastapi import FastAPI
    from human_control_api import create_router
    from human_control_store import ControlStore

    call, _, _, _ = bot_sql
    secret = "synthetic-transfer-" + "x" * 40
    config = SimpleNamespace(CRM_CONTROL_ENABLED=True, HUMAN_SUPPORT_PROVIDER="crm", CRM_BOT_CONTROL_SECRET=secret, BUSINESS_ID="memos")
    store = ControlStore(lambda name, args: call(args["p_action"], args["p_request_id"], args["p_actor"], args["p_expected_version"], args["p_payload"]), "memos")
    def forbidden(*args, **kwargs):
        raise AssertionError("Transfer cannot send messages or change followups")
    app = FastAPI()
    app.include_router(create_router(config, lambda: store, forbidden, forbidden, forbidden, forbidden))
    body = {"actor": RETAIL, "command_id": "endpoint-transfer", "expected_version": 7, "target_actor": WHOLESALE}
    path = f"/internal/crm/v1/businesses/memos/conversations/{PHONE}/transfer"
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 401
        assert client.post(path.replace("/memos/", "/tanaka/"), json=body, headers={"X-CRM-Bot-Secret": secret}).status_code == 403
        response = client.post(path, json=body, headers={"X-CRM-Bot-Secret": secret})
        assert response.status_code == 200 and response.json()["control"]["owner"] == WHOLESALE
        assert response.json()["control"]["is_paused"] is True


@pytest.mark.parametrize("stored,queue,unknown", [
    ({"tipo_cliente": "Mayorista"}, "wholesale", False),
    ({"customer_type": "wholesale"}, "wholesale", False),
    ({"tipo_de_cliente": "Detal"}, "retail", False),
    ({}, "retail", True),
])
def test_reuses_structured_customer_type_without_commercial_inference(stored, queue, unknown):
    assert customer_queue({"customer_data": stored}) == (queue, unknown)


@pytest.fixture
def queue_client(settings, sessions, bot_sql):
    call, _, _, _ = bot_sql
    businesses = {name: Business(name, "", "", bot_api_url=f"https://{name}.synthetic.invalid",
                                  bot_control_secret="synthetic-" + name + "x" * 40, operations_enabled=True)
                  for name in ("tanaka", "velvet", "memos", "briosos")}
    settings = replace(settings, businesses=businesses)
    rows = {name: data() for name in businesses}
    rows["tanaka"]["message_logs"][0]["created_at"] = "2026-11-06T12:02:00+00:00"
    rows["velvet"]["message_logs"][0]["created_at"] = "2026-11-06T12:03:00+00:00"
    worker = Synchronizer(settings, sessions, lambda business: FakeSource(rows[business.id]))
    assert set(worker.run().values()) == {"ok"}
    calls = []

    def transport(request):
        calls.append((request.method, request.url.host, request.url.path))
        if request.url.host == "memos.synthetic.invalid":
            action = request.url.path.rsplit("/", 1)[-1]
            if "/commands/" in request.url.path:
                result = call("command", action)
            elif action == "control":
                result = call("read")
            elif action == "transfer":
                body = json.loads(request.read())
                result = call("transfer", body["command_id"], body["actor"], body["expected_version"], {"target_actor": body["target_actor"]})
                if app.state.transfer_ack_lost:
                    app.state.transfer_ack_lost = False
                    raise httpx.ReadTimeout("Synthetic acknowledgement lost after atomic transfer", request=request)
            else:
                raise AssertionError("Unexpected bot mutation")
            return httpx.Response(result.get("http_status", 200), json={"detail": result["error"]} if "error" in result else result)
        assert request.url.host in {"tanaka.synthetic.invalid", "velvet.synthetic.invalid"}
        business = request.url.host.split(".")[0]
        owner = "operator:velvet"
        control = {"business_id": business, "phone_number": PHONE, "provider": "crm", "phase": "human", "owner": owner,
                   "version": 3, "is_paused": True, "confirmed": True, "can_send": True, "window_expires_at": "2030-01-01T00:00:00Z"}
        if request.method == "GET":
            return httpx.Response(200, json=control)
        body = json.loads(request.read())
        assert body["actor"] == owner
        return httpx.Response(200, json={"command_id": body["command_id"], "status": "accepted", "control": control, "provider_message_id": "synthetic-provider-id"})

    app = create_app(settings)
    app.state.transfer_ack_lost = False
    app.state.bot_transport = httpx.MockTransport(transport)
    with TestClient(app) as client:
        yield client, settings, worker, rows, calls
    app.state.engine.dispose()


def headers(settings, profile, queue=None, role="operator", businesses=None, aliases=None, retired=None):
    return {"Authorization": "Bearer " + token(settings, profile=profile, allowed_businesses=businesses or ["memos"],
        attention_queue=queue, operator_role=role, actor_aliases=aliases or [], queue_actors={"retail": RETAIL, "wholesale": WHOLESALE}, login_profile=profile, retired_profiles=retired or [])}


def test_shared_scope_real_message_order_same_contact_and_legacy_owner(queue_client, sessions, notifications_settings):
    client, settings, _, _, calls = queue_client
    with sessions.begin() as session:
        from crm_service.models import Agent
        session.add(Agent(profile="operator:velvet", allowed_businesses=["velvet"]))
        session.add(PushSubscription(profile="operator:velvet", endpoint_hash="old-velvet", subscription={"endpoint": "https://fcm.googleapis.com/old-velvet", "keys": {}}))
    auth = headers(settings, "operator:tanaka", businesses=["tanaka", "velvet"], aliases=["operator:velvet"], retired=["operator:velvet"])
    response = client.get("/v1/inbox?bucket=awaiting_human&limit=1", headers=auth)
    assert response.status_code == 200 and response.json()["items"][0]["business_id"] == "velvet"
    next_page = client.get("/v1/inbox", params={"bucket": "awaiting_human", "limit": 1, "cursor": response.json()["next_cursor"]}, headers=auth).json()
    assert next_page["items"][0]["business_id"] == "tanaka"
    assert next_page["items"][0]["phone_number"] == response.json()["items"][0]["phone_number"]
    assert client.get(f"/v1/conversations/memos/{PHONE}", headers=auth).status_code == 403
    for business in ("velvet", "tanaka"):
        result = client.post(f"/v1/conversations/{business}/{PHONE}/messages", headers=auth,
                             json={"command_id": "legacy-owner-" + business, "expected_version": 3, "text": "Synthetic"})
        assert result.status_code == 200 and result.json()["status"] == "accepted"
    assert [host for method, host, _ in calls if method == "POST"] == ["velvet.synthetic.invalid", "tanaka.synthetic.invalid"]
    with sessions.begin() as session:
        agent = session.get(Agent, "operator:tanaka")
        assert set(agent.allowed_businesses) == {"tanaka", "velvet"}
        assert session.scalar(select(PushSubscription).where(PushSubscription.profile == "operator:velvet")).active is False
        session.add(PushSubscription(profile=agent.profile, endpoint_hash="shared-operator", subscription={"endpoint": "https://fcm.googleapis.com/shared", "keys": {}, "_login_profile": "tanaka"}))
        for business in ("tanaka", "velvet"):
            crm = session.get(CRMConversation, (business, PHONE))
            crm.transition += 1
            event = NotificationEvent(business_id=business, phone_number=PHONE, transition=crm.transition, link="/chats")
            historical = NotificationEvent(business_id=business, phone_number=PHONE, transition=crm.transition, link="/chats", created_at=now() - timedelta(days=1))
            assert notification_allowed(session, historical, agent, "web_push") is False
            session.add(event)
    assert {item["business_id"] for item in client.get("/v1/notifications", headers=auth).json()["items"]} == {"tanaka", "velvet"}
    admin = headers(settings, "operator", role="admin", businesses=["tanaka", "velvet"])
    assert client.get("/v1/notifications", headers=admin).json()["items"] == []
    assert all(not channel for prefs in client.get("/v1/me/preferences", headers=admin).json().values() for channel in prefs.values())
    recipients = []
    enabled = replace(settings, notifications_enabled=True, vapid_private_key=notifications_settings.vapid_private_key, vapid_subject=notifications_settings.vapid_subject)
    assert dispatch(enabled, sessions, lambda sub, payload, cfg: recipients.append(payload["profile"]) or 201) == 2
    assert recipients == ["tanaka", "tanaka"]


@pytest.mark.parametrize("lost_ack", [False, True])
def test_transfer_coordinates_queue_authority_recovery_and_notification_recipients(queue_client, sessions, notifications_settings, lost_ack):
    client, settings, worker, _, calls = queue_client
    retail, wholesale = headers(settings, RETAIL, "retail"), headers(settings, WHOLESALE, "wholesale")
    admin = headers(settings, "operator", role="admin", businesses=list(settings.businesses))
    for auth in (retail, wholesale, admin):
        client.get("/v1/inbox", headers=auth)
    retail_items = client.get("/v1/inbox", headers=retail).json()["items"]
    assert len(retail_items) == 1 and retail_items[0]["queue_needs_classification"] is True
    assert client.get("/v1/inbox", headers=wholesale).json()["items"] == []
    assert client.get(f"/v1/conversations/memos/{PHONE}/control", headers=wholesale).status_code == 403
    with sessions.begin() as session:
        crm = session.get(CRMConversation, ("memos", PHONE))
        crm.assigned_to = RETAIL
        crm.transition = 1
        for profile in (RETAIL, WHOLESALE, "operator"):
            session.add(PushSubscription(profile=profile, endpoint_hash=profile, subscription={"endpoint": "https://fcm.googleapis.com/synthetic/" + profile, "keys": {}, "_login_profile": profile}))
        old = NotificationEvent(business_id="memos", phone_number=PHONE, transition=0, link="/chats", created_at=now() - timedelta(days=1))
        event = NotificationEvent(business_id="memos", phone_number=PHONE, transition=1, link="/chats")
        session.add_all([old, event])
        session.flush()
        retail_sub = session.scalar(select(PushSubscription).where(PushSubscription.profile == RETAIL))
        session.add(PushDelivery(event_id=event.id, subscription_id=retail_sub.id, status="pending"))
        before = session.scalar(select(SourceMessage).where(SourceMessage.business_id == "memos")).content
    body = {"command_id": "queue-switch", "expected_version": 7, "target_queue": "wholesale"}
    client.app.state.transfer_ack_lost = lost_ack
    moved = client.post(f"/v1/conversations/memos/{PHONE}/transfer", json=body, headers=retail)
    if lost_ack:
        assert moved.status_code == 202 and moved.json()["status"] == "unknown"
        blocked = client.post(f"/v1/conversations/memos/{PHONE}/messages", headers=retail,
                              json={"command_id": "no-send-during-transfer", "expected_version": 8, "text": "blocked"})
        assert blocked.status_code == 409
        moved = client.get(f"/v1/conversations/memos/{PHONE}/commands/queue-switch", headers=retail)
    assert moved.status_code == 200 and moved.json()["status"] == "confirmed"
    assert moved.json()["control"]["owner"] == WHOLESALE and moved.json()["control"]["is_paused"] is True
    assert client.post(f"/v1/conversations/memos/{PHONE}/transfer", json=body, headers=retail).json()["status"] == "confirmed"
    assert client.get(f"/v1/conversations/memos/{PHONE}/commands/queue-switch", headers=retail).json()["status"] == "confirmed"
    assert len([c for c in calls if c[0] == "POST"]) == 1
    assert client.get(f"/v1/conversations/memos/{PHONE}", headers=retail).status_code == 403
    assert client.post(f"/v1/conversations/memos/{PHONE}/messages", json={"command_id": "old-owner-send", "expected_version": 8, "text": "blocked"}, headers=retail).status_code == 403
    assert client.get("/v1/inbox", headers=retail).json()["items"] == []
    assert client.get("/v1/inbox", headers=wholesale).json()["items"][0]["assigned_to"] == WHOLESALE
    with sessions.begin() as session:
        session.get(CRMConversation, ("memos", PHONE)).classification = {**session.get(CRMConversation, ("memos", PHONE)).classification}
        from crm_service.models import SourceConversation
        session.get(SourceConversation, ("memos", PHONE)).dirty = True
    worker.run("memos")
    assert client.get("/v1/inbox", headers=wholesale).json()["items"][0]["attention_queue"] == "wholesale"
    assert client.get("/v1/notifications", headers=retail).json()["items"] == []
    assert len(client.get("/v1/notifications", headers=wholesale).json()["items"]) == 1
    assert client.get("/v1/notifications", headers=admin).json()["items"] == []
    recipients = []
    enabled = replace(settings, notifications_enabled=True, vapid_private_key=notifications_settings.vapid_private_key, vapid_subject=notifications_settings.vapid_subject)
    assert dispatch(enabled, sessions, lambda sub, payload, cfg: recipients.append(payload["profile"]) or 201) == 1
    assert recipients == [WHOLESALE]
    with sessions() as session:
        assert session.scalar(select(SourceMessage).where(SourceMessage.business_id == "memos")).content == before
        assert session.scalar(select(PushDelivery).join(PushSubscription).where(PushSubscription.profile == RETAIL)).status == "disabled"
