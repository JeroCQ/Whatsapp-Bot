"""Pilot acceptance against disposable PostgreSQL, with only Meta/Gemini simulated.

This does not assert that a physical WhatsApp device received a message. Receipt
callbacks are signed synthetic provider events. No production endpoint is used.
"""

import hashlib
import hmac
import json
import os
import time
import types
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock

import httpx
import jwt
import psycopg
import pytest
import requests
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from test_chatwoot_integration import main

ROOT = Path(__file__).parents[1]
BUSINESS = "pilot_alpha"
OTHER_BUSINESS = "pilot_beta"
PHONE = "573000000001"
BOT_SECRET = "synthetic-bot-control-" + "x" * 40
SOURCE_SECRET = "synthetic-bot-source-" + "y" * 40
APP_SECRET = "synthetic-meta-app-" + "z" * 40


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def _wire(value):
    return json.loads(json.dumps(value, default=_jsonable))


class SQLBotRepository:
    """Test transport for the existing Supabase repository, backed by actual PG."""

    TABLES = {
        "customers",
        "conversation_states",
        "message_logs",
        "processed_webhook_events",
        "handoff_deliveries",
        "catalog_assets",
    }

    def __init__(self, dsn):
        self.dsn = dsn

    def table(self, name):
        if name not in self.TABLES:
            raise ValueError("Fixture table is outside the bot schema")
        return SQLQuery(self, name)

    def dispatch(self, name, parameters):
        assert name == "crm_control_dispatch"
        assignments = sql.SQL(", ").join(
            sql.SQL("{} => %s").format(sql.Identifier(key)) for key in parameters
        )
        values = [
            Jsonb(value) if isinstance(value, (dict, list)) else value
            for value in parameters.values()
        ]
        with psycopg.connect(
            self.dsn, autocommit=True, row_factory=dict_row
        ) as connection:
            row = connection.execute(
                sql.SQL("SELECT public.crm_control_dispatch({}) AS result").format(
                    assignments
                ),
                values,
            ).fetchone()
            return _wire(row["result"])

    def rpc(self, name, parameters):
        return types.SimpleNamespace(
            execute=lambda: types.SimpleNamespace(data=self.dispatch(name, parameters))
        )


class SQLQuery:
    def __init__(self, repository, table):
        self.repository, self.name = repository, table
        self.mode, self.columns = "select", "*"
        self.values, self.filters, self.ordering = None, [], []
        self.bound = None
        self.conflict, self.ignore_duplicates = None, False

    def select(self, columns="*"):
        self.columns = columns
        return self

    def insert(self, values):
        self.mode, self.values = "insert", values
        return self

    def upsert(self, values, *, on_conflict, ignore_duplicates=False):
        self.mode, self.values = "upsert", values
        self.conflict, self.ignore_duplicates = on_conflict, ignore_duplicates
        return self

    def update(self, values):
        self.mode, self.values = "update", values
        return self

    def delete(self):
        self.mode = "delete"
        return self

    def eq(self, key, value):
        self.filters.append((key, "=", value))
        return self

    def gt(self, key, value):
        self.filters.append((key, ">", value))
        return self

    def is_(self, key, value):
        assert value in (None, "null")
        self.filters.append((key, "IS NULL", None))
        return self

    def order(self, key, desc=False):
        self.ordering.append((key, desc))
        return self

    def limit(self, count):
        self.bound = int(count)
        return self

    def execute(self):
        parameters = []
        table = sql.Identifier("public", self.name)
        if self.mode == "select":
            columns = (
                sql.SQL("*")
                if self.columns == "*"
                else sql.SQL(",").join(
                    sql.Identifier(column) for column in self.columns.split(",")
                )
            )
            statement = sql.SQL("SELECT {} FROM {}").format(columns, table)
        elif self.mode in {"insert", "upsert"}:
            assert isinstance(self.values, dict)
            keys = list(self.values)
            parameters.extend(
                Jsonb(self.values[k])
                if isinstance(self.values[k], (dict, list))
                else self.values[k]
                for k in keys
            )
            statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                table,
                sql.SQL(",").join(map(sql.Identifier, keys)),
                sql.SQL(",").join(sql.Placeholder() for _ in keys),
            )
            if self.mode == "upsert":
                conflict = sql.SQL(",").join(
                    map(sql.Identifier, self.conflict.split(","))
                )
                action = (
                    sql.SQL("NOTHING")
                    if self.ignore_duplicates
                    else sql.SQL("UPDATE SET ")
                    + sql.SQL(",").join(
                        sql.SQL("{} = EXCLUDED.{}").format(
                            sql.Identifier(key), sql.Identifier(key)
                        )
                        for key in keys
                    )
                )
                statement += sql.SQL(" ON CONFLICT ({}) DO ").format(conflict) + action
        elif self.mode == "update":
            keys = list(self.values)
            parameters.extend(
                Jsonb(self.values[k])
                if isinstance(self.values[k], (dict, list))
                else self.values[k]
                for k in keys
            )
            statement = sql.SQL("UPDATE {} SET {}").format(
                table,
                sql.SQL(",").join(
                    sql.SQL("{} = %s").format(sql.Identifier(key)) for key in keys
                ),
            )
        else:
            statement = sql.SQL("DELETE FROM {}").format(table)
        if self.filters:
            expressions = []
            for key, operator, value in self.filters:
                expression = sql.Identifier(key) + sql.SQL(" " + operator)
                if operator != "IS NULL":
                    expression += sql.SQL(" %s")
                    parameters.append(value)
                expressions.append(expression)
            statement += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(expressions)
        if self.mode == "select":
            if self.ordering:
                statement += sql.SQL(" ORDER BY ") + sql.SQL(",").join(
                    sql.Identifier(key) + sql.SQL(" DESC" if desc else " ASC")
                    for key, desc in self.ordering
                )
            if self.bound is not None:
                statement += sql.SQL(" LIMIT %s")
                parameters.append(self.bound)
        else:
            statement += sql.SQL(" RETURNING *")
        with psycopg.connect(
            self.repository.dsn, autocommit=True, row_factory=dict_row
        ) as connection:
            return types.SimpleNamespace(
                data=_wire(connection.execute(statement, parameters).fetchall())
            )


class SyntheticMeta:
    def __init__(self):
        self.sent, self.mode = [], "accepted"
        self.lock = Lock()
        self.entered, self.release = Event(), Event()

    def post(self, url, *, headers, json, **kwargs):
        assert url.startswith("https://graph.facebook.com/")
        with self.lock:
            message_id = "wamid.SYNTHETIC." + str(len(self.sent) + 1)
            row = {
                "id": message_id,
                "payload": json,
                "accepted": self.mode != "rejected",
            }
            self.sent.append(row)
        if self.mode == "blocking":
            self.entered.set()
            assert self.release.wait(5), "Synthetic in-flight send was not released"
        if self.mode == "ambiguous":
            raise requests.exceptions.ReadTimeout(
                "SYNTHETIC ambiguous provider outcome"
            )
        if self.mode == "rejected":
            return types.SimpleNamespace(
                status_code=400,
                json=lambda: {
                    "error": {"code": 131030, "message": "SYNTHETIC rejection"}
                },
            )
        return types.SimpleNamespace(
            status_code=200, json=lambda: {"messages": [{"id": message_id}]}
        )


def _jwt(settings, allowed=None, profile="pilot.employee.one"):
    issued = int(time.time())
    return {
        "Authorization": "Bearer "
        + jwt.encode(
            {
                "iss": "chatty-catalog-compass",
                "aud": "whatsapp-crm",
                "profile": profile,
                "allowed_businesses": allowed if allowed is not None else [BUSINESS],
                "iat": issued,
                "exp": issued + 120,
            },
            settings.jwt_secret,
            algorithm="HS256",
        )
    }


def _signed(value):
    body = json.dumps(value, separators=(",", ":")).encode()
    signature = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": "sha256=" + signature,
    }


def _incoming(message_id="wamid.SYNTHETIC.incoming", body="Necesito un asesor"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "pilot-number"},
                            "contacts": [
                                {"wa_id": PHONE, "profile": {"name": "SYNTHETIC pilot"}}
                            ],
                            "messages": [
                                {
                                    "id": message_id,
                                    "from": PHONE,
                                    "type": "text",
                                    "timestamp": str(int(time.time())),
                                    "text": {"body": body},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }


def _receipt(message, status="delivered"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "pilot-number"},
                            "statuses": [
                                {
                                    "id": message["id"],
                                    "recipient_id": PHONE,
                                    "status": status,
                                    "timestamp": str(int(time.time())),
                                    "biz_opaque_callback_data": message["payload"].get(
                                        "biz_opaque_callback_data"
                                    ),
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }


SOURCE_DDL = """
CREATE TABLE customers(phone_number text PRIMARY KEY,name text,created_at timestamptz DEFAULT now());
CREATE TABLE conversation_states(phone_number text PRIMARY KEY REFERENCES customers(phone_number),
 current_state text DEFAULT 'GREETING',is_paused boolean DEFAULT false,handoff_reason text,
 last_interaction timestamptz DEFAULT now(),chatwoot_conversation_id bigint,
 follow_up_token text,customer_data jsonb DEFAULT '{}',order_summary text,
 follow_up_not_before timestamptz,follow_up_suppressed boolean DEFAULT false,
 follow_up_suppression_reason text);
CREATE TABLE message_logs(id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
 phone_number text REFERENCES customers(phone_number),role text,content text,
 created_at timestamptz DEFAULT now(),metadata jsonb DEFAULT '{}',event_key text UNIQUE);
CREATE TABLE processed_webhook_events(source text,event_id text,phone_number text,status text,
 received_at timestamptz DEFAULT now(),processed_at timestamptz,error text,PRIMARY KEY(source,event_id));
CREATE TABLE handoff_deliveries(business_id text,phone_number text,request_id text,status text,
 attempts integer DEFAULT 0,retry_at timestamptz DEFAULT now(),conversation_id bigint,last_error text,
 created_at timestamptz DEFAULT now(),updated_at timestamptz DEFAULT now(),PRIMARY KEY(business_id,phone_number));
"""


@pytest.fixture
def pilot(monkeypatch):
    """Two separate disposable databases; CRM has only a scoped bot API secret."""
    from alembic import command
    from alembic.config import Config
    from crm_service.api import create_app
    from crm_service.settings import Business, Settings

    import database as bot_database
    from human_control_store import ControlStore

    base = make_url(os.environ["CRM_TEST_DATABASE_URL"])
    assert base.host in {"localhost", "127.0.0.1", "::1"} and not base.query
    assert (base.database or "").startswith("crm_test")
    suffix = uuid.uuid4().hex[:12]
    names = ["crm_test_pilot_bot_" + suffix, "crm_test_pilot_crm_" + suffix]
    admin = create_engine(
        base.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
    )
    bot_url, crm_url = [
        base.set(database=name).render_as_string(hide_password=False) for name in names
    ]
    bot_dsn = (
        make_url(bot_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    repository = SQLBotRepository(bot_dsn)
    store = ControlStore(repository.dispatch, BUSINESS)
    meta = SyntheticMeta()
    crm_app = None
    try:
        with admin.connect() as connection:
            for name in names:
                connection.execute(text(f'CREATE DATABASE "{name}"'))
        bootstrap = (ROOT / "supabase/bootstrap.sql").read_text()
        section = bootstrap.split("-- CRM CONTROL BEGIN", 1)[1].split(
            "-- CRM CONTROL END", 1
        )[0]
        upgrade = (ROOT / "supabase/upgrade_existing_brand.sql").read_text()
        assert (
            section
            == upgrade.split("-- CRM CONTROL BEGIN", 1)[1].split(
                "-- CRM CONTROL END", 1
            )[0]
        )
        with psycopg.connect(bot_dsn, autocommit=True) as connection:
            connection.execute(SOURCE_DDL)
            connection.execute(section)
            connection.execute(
                section
            )  # Re-running the reviewed upgrade is idempotent.
        monkeypatch.setenv("CRM_DATABASE_URL", crm_url)
        command.upgrade(Config(str(ROOT / "crm/alembic.ini")), "head")
        for key, value in {
            "BUSINESS_ID": BUSINESS,
            "HUMAN_SUPPORT_PROVIDER": "crm",
            "CRM_CONTROL_ENABLED": True,
            "CRM_BOT_CONTROL_SECRET": BOT_SECRET,
            "CRM_SOURCE_READ_ENABLED": True,
            "CRM_SOURCE_READ_SECRET": SOURCE_SECRET,
            "CHATWOOT_ENABLED": False,
            "REDIS_URL": None,
            "WA_PHONE_NUMBER_ID": "pilot-number",
            "WA_APP_SECRET": APP_SECRET,
        }.items():
            monkeypatch.setattr(main.config, key, value, raising=False)
        monkeypatch.setattr(bot_database, "supabase", repository)
        monkeypatch.setattr(main, "get_control_store", lambda: store)
        monkeypatch.setattr(main, "post", meta.post)
        monkeypatch.setattr(main, "queue_enabled", lambda: False)
        for name in (
            "get_or_create_contact",
            "create_conversation",
            "send_message_to_chatwoot",
        ):
            monkeypatch.setattr(
                main.chatwoot_api,
                name,
                lambda *a, **kw: pytest.fail("CRM pilot must not contact Chatwoot"),
            )
        llm_calls = []

        def gemini(phone, body, *args):
            llm_calls.append(body)
            handoff = body == "Necesito un asesor"
            if handoff:
                bot_database.pause_bot_for_handoff(
                    phone, "SYNTHETIC customer requested human"
                )
            return types.SimpleNamespace(
                response="" if handoff else "SYNTHETIC AI response",
                requested_files=[],
                send_files_before_response=False,
                follow_up_message="",
                handoff_requested=handoff,
            )

        monkeypatch.setattr(main, "process_message_logic", gemini)
        settings = Settings(
            database_url=crm_url,
            jwt_secret="synthetic-pilot-jwt-" + "j" * 40,
            sync_secret="synthetic-pilot-sync-" + "s" * 40,
            businesses={
                BUSINESS: Business(
                    BUSINESS,
                    "",
                    "",
                    bot_api_url="https://pilot-bot.example",
                    bot_control_secret=BOT_SECRET,
                    bot_source_secret=SOURCE_SECRET,
                    operations_enabled=True,
                    bot_api_enabled=True,
                ),
                OTHER_BUSINESS: Business(
                    OTHER_BUSINESS,
                    "",
                    "",
                    bot_api_url="https://other-pilot-bot.example",
                    bot_control_secret="synthetic-other-" + "o" * 40,
                    operations_enabled=True,
                ),
            },
            source_mode="bot_api",
            notifications_enabled=False,
        )
        with TestClient(main.app) as bot_client:
            requests_to_bot = []

            def transport(request):
                requests_to_bot.append((request.method, request.url.path))
                response = bot_client.request(
                    request.method,
                    request.url.path
                    + ("?" + request.url.query.decode() if request.url.query else ""),
                    content=request.content,
                    headers=dict(request.headers),
                )
                return httpx.Response(
                    response.status_code,
                    content=response.content,
                    headers=dict(response.headers),
                )

            crm_app = create_app(settings)
            crm_app.state.bot_transport = httpx.MockTransport(transport)
            with TestClient(crm_app) as crm_client:
                yield types.SimpleNamespace(
                    bot=bot_client,
                    crm=crm_client,
                    settings=settings,
                    repository=repository,
                    store=store,
                    meta=meta,
                    llm_calls=llm_calls,
                    requests_to_bot=requests_to_bot,
                    bot_dsn=bot_dsn,
                    transport=httpx.MockTransport(transport),
                    app=crm_app,
                )
    finally:
        if crm_app is not None:
            crm_app.state.engine.dispose()
        with admin.connect() as connection:
            for name in names:
                connection.execute(
                    text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
                )
        admin.dispose()


def _post_incoming(
    pilot, *, message_id="wamid.SYNTHETIC.incoming", body="Necesito un asesor"
):
    encoded, headers = _signed(_incoming(message_id, body))
    response = pilot.bot.post("/webhook", content=encoded, headers=headers)
    assert response.status_code == 200 and response.json()["accepted"] == 1
    return response


def _control(pilot):
    response = pilot.crm.get(
        f"/v1/conversations/{BUSINESS}/{PHONE}/control", headers=_jwt(pilot.settings)
    )
    assert response.status_code == 200, response.text
    return response.json()


def _command(pilot, operation, command_id, *, version=None, text_value=None):
    if version is None:
        version = _control(pilot)["version"]
    body = {"command_id": command_id, "expected_version": version}
    if text_value is not None:
        body["text"] = text_value
    else:
        body["reason"] = "SYNTHETIC acceptance"
    return pilot.crm.post(
        f"/v1/conversations/{BUSINESS}/{PHONE}/{operation}",
        json=body,
        headers=_jwt(pilot.settings),
    )


def _take(pilot, command_id="take-acceptance"):
    response = _command(pilot, "take", command_id)
    assert response.status_code in (200, 202), response.text
    assert response.json()["control"]["confirmed"] is True
    return response.json()["control"]


def _command_status(pilot, command_id):
    response = pilot.crm.get(
        f"/v1/conversations/{BUSINESS}/{PHONE}/commands/{command_id}",
        headers=_jwt(pilot.settings),
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("legacy_chatwoot_id", [None, 44])
def test_client_handoff_pause_human_receipt_close_and_ai_resume(pilot, legacy_chatwoot_id):
    if legacy_chatwoot_id is not None:
        # A migrated business keeps its old ticket reference after a CRM close.
        # That reference must neither hold AI control nor swallow a new handoff.
        import database as bot_database

        bot_database.get_or_create_customer_state(PHONE, "SYNTHETIC contact")
        with psycopg.connect(pilot.bot_dsn, autocommit=True) as connection:
            connection.execute(
                "UPDATE conversation_states SET chatwoot_conversation_id=%s WHERE phone_number=%s",
                (legacy_chatwoot_id, PHONE),
            )
    _post_incoming(pilot)
    control = _control(pilot)
    assert control["phase"] == "waiting" and control["is_paused"]
    _take(pilot)
    assert _control(pilot)["can_send"]
    response = _command(
        pilot, "messages", "human-reply-acceptance", text_value="SYNTHETIC human answer"
    )
    assert response.status_code in (200, 202), response.text
    assert response.json()["status"] == "accepted"
    accepted = pilot.meta.sent[-1]
    assert _command_status(pilot, "human-reply-acceptance")["status"] == "accepted"
    encoded, headers = _signed(_receipt(accepted))
    assert (
        pilot.bot.post("/webhook", content=encoded, headers=headers).status_code == 200
    )
    assert _command_status(pilot, "human-reply-acceptance")["status"] == "delivered"
    logs = (
        pilot.repository.table("message_logs")
        .select("role,content")
        .eq("phone_number", PHONE)
        .execute()
        .data
    )
    assert (
        sum(
            row["role"] == "asesor" and row["content"] == "SYNTHETIC human answer"
            for row in logs
        )
        == 1
    )
    closed = _command(pilot, "close", "close-acceptance")
    assert (
        closed.status_code in (200, 202) and closed.json()["control"]["phase"] == "ai"
    ), closed.text
    count = len(pilot.meta.sent)
    _post_incoming(
        pilot, message_id="wamid.SYNTHETIC.after-close", body="Hola otra vez"
    )
    assert len(pilot.meta.sent) == count + 1
    assert pilot.meta.sent[-1]["payload"]["text"]["body"] == "SYNTHETIC AI response"
    assert "Hola otra vez" in pilot.llm_calls
    _post_incoming(
        pilot, message_id="wamid.SYNTHETIC.handoff-again", body="Necesito un asesor"
    )
    assert _control(pilot)["phase"] == "waiting"
    if legacy_chatwoot_id is not None:
        row = pilot.repository.table("conversation_states").select("chatwoot_conversation_id").eq(
            "phone_number", PHONE
        ).execute().data[0]
        assert row["chatwoot_conversation_id"] == legacy_chatwoot_id


def test_double_click_and_retry_send_once_and_cross_business_rejected(pilot):
    _post_incoming(pilot)
    _take(pilot)
    version = _control(pilot)["version"]
    count = len(pilot.meta.sent)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: _command(
                    pilot,
                    "messages",
                    "double-click",
                    version=version,
                    text_value="SYNTHETIC once",
                ),
                range(2),
            )
        )
    assert all(response.status_code in (200, 202) for response in responses), [
        r.text for r in responses
    ]
    assert len(pilot.meta.sent) == count + 1
    assert _command(
        pilot, "messages", "double-click", version=version, text_value="SYNTHETIC once"
    ).status_code in (200, 202)
    assert len(pilot.meta.sent) == count + 1
    changed = _command(
        pilot,
        "messages",
        "double-click",
        version=version,
        text_value="SYNTHETIC changed",
    )
    assert changed.status_code == 409
    count_requests = len(pilot.requests_to_bot)
    other = pilot.crm.post(
        f"/v1/conversations/{OTHER_BUSINESS}/{PHONE}/take",
        json={"command_id": "cross-business", "expected_version": 0},
        headers=_jwt(pilot.settings),
    )
    assert other.status_code == 403 and len(pilot.requests_to_bot) == count_requests


@pytest.mark.parametrize(
    "mode,expected", [("rejected", "failed"), ("ambiguous", "unknown")]
)
def test_failed_or_ambiguous_send_is_not_automatically_replayed(pilot, mode, expected):
    _post_incoming(pilot)
    _take(pilot)
    version = _control(pilot)["version"]
    pilot.meta.mode = mode
    count = len(pilot.meta.sent)
    response = _command(
        pilot,
        "messages",
        "failure-acceptance",
        version=version,
        text_value="SYNTHETIC failure",
    )
    assert response.status_code in (200, 202), response.text
    assert response.json()["status"] == expected
    repeated = _command(
        pilot,
        "messages",
        "failure-acceptance",
        version=version,
        text_value="SYNTHETIC failure",
    )
    assert repeated.status_code in (200, 202), repeated.text
    assert len(pilot.meta.sent) == count + 1
    if mode == "ambiguous":
        assert _command(pilot, "close", "close-while-unknown").status_code == 409
        encoded, headers = _signed(_receipt(pilot.meta.sent[-1]))
        assert (
            pilot.bot.post("/webhook", content=encoded, headers=headers).status_code
            == 200
        )
        assert _command_status(pilot, "failure-acceptance")["status"] == "delivered"


def test_inflight_ai_send_blocks_human_send_until_pause_confirmed(pilot):
    _post_incoming(pilot, body="Hola IA")
    pilot.meta.mode = "blocking"
    with ThreadPoolExecutor(max_workers=2) as pool:
        inbound = pool.submit(
            _post_incoming,
            pilot,
            message_id="wamid.SYNTHETIC.inflight",
            body="Otra consulta IA",
        )
        assert pilot.meta.entered.wait(5)
        take = pool.submit(_command, pilot, "take", "take-concurrent-ai")
        # A take may return pausing immediately or wait for the preexisting phone lock.
        time.sleep(0.1)
        if take.done():
            interim = take.result()
            assert interim.status_code in (200, 202), interim.text
            assert not interim.json()["control"]["confirmed"]
            assert not _control(pilot)["can_send"]
            assert (
                _command(
                    pilot, "messages", "too-early", text_value="SYNTHETIC premature"
                ).status_code
                == 409
            )
        pilot.meta.release.set()
        inbound.result(timeout=5)
        response = take.result(timeout=5)
        assert response.status_code in (200, 202), response.text
    final = _control(pilot)
    assert final["confirmed"] is True and final["phase"] == "human"
    count = len(pilot.meta.sent)
    _post_incoming(
        pilot, message_id="wamid.SYNTHETIC.after-take", body="Cliente después de pausa"
    )
    assert len(pilot.meta.sent) == count


def test_untrusted_receipt_cannot_claim_delivery(pilot):
    _post_incoming(pilot)
    _take(pilot)
    response = _command(
        pilot, "messages", "signed-receipt", text_value="SYNTHETIC signing"
    )
    assert response.json()["status"] == "accepted"
    bad = pilot.bot.post("/webhook", json=_receipt(pilot.meta.sent[-1]))
    assert bad.status_code in (401, 403)
    assert _command_status(pilot, "signed-receipt")["status"] == "accepted"


def test_real_bot_export_populates_crm_and_tracks_cycle(pilot):
    from crm_service.bot_source import BotSourceAdapter
    from crm_service.sync import Synchronizer

    def sync():
        worker = Synchronizer(
            pilot.settings,
            pilot.app.state.sessions,
            lambda business: BotSourceAdapter(business, transport=pilot.transport),
        )
        assert worker.run(BUSINESS)[BUSINESS] == "ok"
        result = pilot.crm.get(
            "/v1/inbox",
            params={"bucket": "all", "business_id": BUSINESS},
            headers=_jwt(pilot.settings),
        )
        assert result.status_code == 200
        return result.json()["items"][0]

    _post_incoming(pilot)
    assert sync()["bucket"] == "awaiting_human"
    _post_incoming(
        pilot, message_id="wamid.SYNTHETIC.second-question", body="Una segunda pregunta"
    )
    _take(pilot)
    assert (
        _command(
            pilot, "messages", "export-reply", text_value="SYNTHETIC export reply"
        ).json()["status"]
        == "accepted"
    )
    answered = sync()
    assert answered["bucket"] == "awaiting_human"
    assert answered["priority_reason"] == "human_waiting_customer"
    human = (
        pilot.repository.table("message_logs")
        .select("*")
        .eq("role", "asesor")
        .execute()
        .data[0]
    )
    assert set(human["metadata"]["inbound_message_ids"]) == {
        "wamid.SYNTHETIC.incoming",
        "wamid.SYNTHETIC.second-question",
    }
    assert _command(pilot, "close", "export-close").status_code == 200
    assert sync()["bucket"] == "closed"
    _post_incoming(
        pilot, message_id="wamid.SYNTHETIC.export-next", body="Otra consulta"
    )
    assert sync()["bucket"] == "ai_active"
    denied = pilot.bot.get(
        f"/internal/crm/v1/businesses/{OTHER_BUSINESS}/source/customers",
        headers={"X-CRM-Source-Secret": SOURCE_SECRET},
    )
    assert denied.status_code == 403
    assert (
        pilot.bot.post(
            f"/internal/crm/v1/businesses/{BUSINESS}/source/customers", json={}
        ).status_code
        == 405
    )


def test_queued_old_inbound_is_fenced_after_take_and_close(pilot, monkeypatch):
    queued = []
    original = main._dispatch_before_ack
    monkeypatch.setattr(
        main,
        "_dispatch_before_ack",
        lambda background, fn, *args, **kw: queued.append((fn, args, kw)) or "queued",
    )
    _post_incoming(
        pilot, message_id="wamid.SYNTHETIC.queued-before-take", body="Consulta vieja"
    )
    assert queued and queued[0][2]["crm_control_version"] == 0
    _take(pilot)
    assert _command(pilot, "close", "close-old-queued").status_code == 200
    count = len(pilot.meta.sent)
    fn, args, kwargs = queued.pop(0)
    fn(*args, **kwargs)
    assert len(pilot.meta.sent) == count and not pilot.llm_calls
    monkeypatch.setattr(main, "_dispatch_before_ack", original)
    _post_incoming(
        pilot, message_id="wamid.SYNTHETIC.fresh-after-close", body="Consulta nueva"
    )
    assert len(pilot.meta.sent) == count + 1


def test_expired_window_and_browser_fields_cannot_send(pilot):
    _post_incoming(pilot)
    _take(pilot)
    with psycopg.connect(pilot.bot_dsn, autocommit=True) as c:
        c.execute(
            "UPDATE message_logs SET metadata=jsonb_set(metadata,'{received_at}',to_jsonb((now()-interval '25 hours')::text)) WHERE role='user'"
        )
    count = len(pilot.meta.sent)
    rejected = _command(
        pilot, "messages", "window-expired", text_value="SYNTHETIC forbidden window"
    )
    assert rejected.status_code == 409 and len(pilot.meta.sent) == count
    body = {
        "command_id": "malicious-url",
        "expected_version": _control(pilot)["version"],
        "text": "test",
        "url": "https://not-the-bot.example",
        "actor": "forged",
    }
    assert (
        pilot.crm.post(
            f"/v1/conversations/{BUSINESS}/{PHONE}/messages",
            json=body,
            headers=_jwt(pilot.settings),
        ).status_code
        == 422
    )


def test_old_followups_and_customer_reset_cannot_release_human_control(
    pilot, monkeypatch
):
    _post_incoming(pilot)
    before = _control(pilot)["version"]
    _take(pilot)
    monkeypatch.setattr(main, "follow_ups_enabled", lambda: True)
    assert (
        main.send_scheduled_follow_up(PHONE, "synthetic-token", "SYNTHETIC reminder")[
            "status"
        ]
        == "legacy_missing_control_epoch"
    )
    assert (
        main.send_scheduled_follow_up(
            PHONE, "synthetic-token", "SYNTHETIC reminder", crm_control_version=before
        )["status"]
        == "human_control_epoch_changed"
    )
    monkeypatch.setattr(main.config, "HUMAN_SUPPORT_PROVIDER", "bot_only")
    main._process_whatsapp_message_unlocked(PHONE, "SYNTHETIC contact", "/reset")
    state = (
        pilot.repository.table("conversation_states")
        .select("*")
        .eq("phone_number", PHONE)
        .execute()
        .data[0]
    )
    assert state["is_paused"] and state["crm_control_phase"] == "human"
    assert pilot.meta.sent == []


def test_sql_upgrade_preserves_control_and_denies_public_execution(pilot):
    _post_incoming(pilot)
    taken = _take(pilot)
    upgrade = (ROOT / "supabase/upgrade_existing_brand.sql").read_text()
    section = upgrade.split("-- CRM CONTROL BEGIN", 1)[1].split(
        "-- CRM CONTROL END", 1
    )[0]
    with psycopg.connect(pilot.bot_dsn) as connection:
        connection.execute(section)
        flags = connection.execute(
            "SELECT relrowsecurity FROM pg_class WHERE oid IN ('public.crm_control_commands'::regclass,'public.crm_send_attempts'::regclass)"
        ).fetchall()
        assert flags == [(True,), (True,)]
        assert (
            connection.execute(
                "SELECT count(*) FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a WHERE p.oid IN ('public.crm_control_view(text,text)'::regprocedure,'public.crm_control_dispatch(text,text,text,text,text,bigint,jsonb)'::regprocedure) AND a.grantee=0"
            ).fetchone()[0]
            == 0
        )
    assert _control(pilot)["version"] == taken["version"]
    assert _control(pilot)["confirmed"]


def test_full_canonical_install_and_upgrade_preserve_data_and_revoke_browser_roles():
    """Supabase auth/storage are local stubs; execute BOTH complete canonical scripts."""
    base = make_url(os.environ["CRM_TEST_DATABASE_URL"])
    assert base.host in {"localhost", "127.0.0.1", "::1"} and not base.query
    assert (base.database or "").startswith("crm_test")
    name = "crm_test_pilot_install_" + uuid.uuid4().hex[:12]
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    dsn = base.set(drivername="postgresql", database=name).render_as_string(
        hide_password=False
    )
    connection = None
    try:
        with admin.connect() as active:
            active.execute(text(f'CREATE DATABASE "{name}"'))
        connection = psycopg.connect(dsn)
        # Roles are created only inside this uncommitted transaction and rolled back.
        connection.execute(
            "DO $$ BEGIN IF to_regrole('anon') IS NULL THEN CREATE ROLE anon; END IF; IF to_regrole('authenticated') IS NULL THEN CREATE ROLE authenticated; END IF; IF to_regrole('service_role') IS NULL THEN CREATE ROLE service_role; END IF; END $$"
        )
        connection.execute(
            "CREATE SCHEMA auth; CREATE TABLE auth.users(id uuid PRIMARY KEY); CREATE SCHEMA storage; CREATE TABLE storage.buckets(id text PRIMARY KEY,name text,public boolean,file_size_limit bigint)"
        )
        connection.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO anon,authenticated"
        )
        connection.execute((ROOT / "supabase/bootstrap.sql").read_text())
        connection.execute(
            "INSERT INTO customers(phone_number,name) VALUES (%s,'SYNTHETIC preserved')",
            (PHONE,),
        )
        connection.execute(
            "INSERT INTO conversation_states(phone_number) VALUES (%s)", (PHONE,)
        )
        connection.execute(
            "INSERT INTO message_logs(phone_number,role,content,metadata) VALUES (%s,'user','SYNTHETIC original',%s)",
            (
                PHONE,
                Jsonb(
                    {
                        "received_at": datetime.now(timezone.utc).isoformat(),
                        "source_event_id": "synthetic-preserved",
                    }
                ),
            ),
        )
        signature = "public.crm_control_dispatch(text,text,text,text,text,bigint,jsonb)"
        for _ in range(2):
            connection.execute(
                (ROOT / "supabase/upgrade_existing_brand.sql").read_text()
            )
        assert (
            connection.execute(
                "SELECT name FROM customers WHERE phone_number=%s", (PHONE,)
            ).fetchone()[0]
            == "SYNTHETIC preserved"
        )
        assert (
            connection.execute(
                "SELECT content FROM message_logs WHERE phone_number=%s", (PHONE,)
            ).fetchone()[0]
            == "SYNTHETIC original"
        )
        for role in ("anon", "authenticated"):
            assert not connection.execute(
                "SELECT has_function_privilege(%s,%s,'EXECUTE')", (role, signature)
            ).fetchone()[0]
            assert not connection.execute(
                "SELECT has_table_privilege(%s,'public.crm_send_attempts','SELECT,INSERT,UPDATE,DELETE')",
                (role,),
            ).fetchone()[0]
        assert connection.execute(
            "SELECT has_function_privilege('service_role',%s,'EXECUTE')", (signature,)
        ).fetchone()[0]
        value = connection.execute(
            "SELECT public.crm_control_dispatch(%s,%s,'read')", (BUSINESS, PHONE)
        ).fetchone()[0]
        assert value["phase"] == "ai" and not value["is_paused"]
    finally:
        if connection is not None:
            connection.rollback()
            connection.close()
        with admin.connect() as active:
            active.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_failed_delivery_updates_inbox_incrementally_and_late_sent_cannot_erase_it(
    pilot,
):
    from crm_service.bot_source import BotSourceAdapter
    from crm_service.models import SourceMessage, SyncCursor
    from crm_service.sync import Synchronizer
    from sqlalchemy import select

    worker = Synchronizer(
        pilot.settings,
        pilot.app.state.sessions,
        lambda business: BotSourceAdapter(business, transport=pilot.transport),
    )

    def sync():
        assert worker.run(BUSINESS)[BUSINESS] == "ok"
        response = pilot.crm.get(
            "/v1/inbox",
            params={"bucket": "all", "business_id": BUSINESS},
            headers=_jwt(pilot.settings),
        )
        assert response.status_code == 200
        return response.json()["items"][0]

    _post_incoming(pilot)
    _take(pilot)
    response = _command(
        pilot,
        "messages",
        "incremental-failed-reply",
        text_value="SYNTHETIC accepted then failed",
    )
    assert response.json()["status"] == "accepted"
    provider_message = pilot.meta.sent[-1]
    answered = sync()
    assert answered["bucket"] == "awaiting_human"
    assert answered["priority_reason"] == "human_waiting_customer"
    accepted = (
        sync()
    )  # Advance the ordinary rolling-reconciliation deadline before the callback.
    with pilot.app.state.sessions.begin() as session:
        cursor = session.get(SyncCursor, (BUSINESS, "message_reconciliation"))
        if cursor is None:
            # The bot API's live lane need not initialize the legacy historical
            # sweep. Explicitly postpone it to prove the new receipt arrives
            # incrementally rather than depending on a historical reread.
            from datetime import timedelta

            cursor = SyncCursor(business_id=BUSINESS, resource="message_reconciliation", value={
                "id": 0, "next_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
            })
            session.add(cursor)
        assert cursor and datetime.fromisoformat(
            cursor.value["next_at"]
        ) > datetime.now(timezone.utc)
        human = session.scalar(
            select(SourceMessage).where(
                SourceMessage.business_id == BUSINESS, SourceMessage.role == "asesor"
            )
        )
        assert human.message_metadata["delivery_status"] == "accepted"
    assert (
        pilot.crm.post(
            f"/v1/conversations/{BUSINESS}/{PHONE}/read",
            json={"read": True},
            headers=_jwt(pilot.settings),
        ).status_code
        == 200
    )
    for status in ("failed", "failed", "sent"):
        encoded, headers = _signed(_receipt(provider_message, status))
        assert (
            pilot.bot.post("/webhook", content=encoded, headers=headers).status_code
            == 200
        )
    assert _command_status(pilot, "incremental-failed-reply")["status"] == "failed"
    failed = sync()
    assert (
        failed["bucket"] == "awaiting_human"
        and failed["unanswered_customer_count"] == 1
    )
    assert failed["read"] and failed["last_message_id"] == accepted["last_message_id"]
    with pilot.app.state.sessions() as session:
        human = session.scalar(
            select(SourceMessage).where(
                SourceMessage.business_id == BUSINESS, SourceMessage.role == "asesor"
            )
        )
        assert (
            human.message_metadata["delivery_status"] == "accepted"
        )  # The new receipt fact, not a historical reread, changed classification.
    facts = (
        pilot.repository.table("message_logs")
        .select("*")
        .eq("phone_number", PHONE)
        .execute()
        .data
    )
    statuses = [
        row["metadata"]["delivery_status"]
        for row in facts
        if row["metadata"].get("source") == "crm_delivery"
    ]
    assert statuses.count("failed") == 1 and statuses.count("accepted") == 1


def test_delivery_receipts_after_close_do_not_reopen_or_mark_unread(pilot):
    from crm_service.bot_source import BotSourceAdapter
    from crm_service.sync import Synchronizer

    worker = Synchronizer(
        pilot.settings,
        pilot.app.state.sessions,
        lambda business: BotSourceAdapter(business, transport=pilot.transport),
    )

    def sync():
        assert worker.run(BUSINESS)[BUSINESS] == "ok"
        response = pilot.crm.get(
            "/v1/inbox",
            params={"bucket": "all", "business_id": BUSINESS},
            headers=_jwt(pilot.settings),
        )
        assert response.status_code == 200
        return response.json()["items"][0]

    _post_incoming(pilot)
    _take(pilot)
    assert (
        _command(
            pilot,
            "messages",
            "closed-receipt-reply",
            text_value="SYNTHETIC close with pending receipt",
        ).json()["status"]
        == "accepted"
    )
    provider_message = pilot.meta.sent[-1]
    assert _command(pilot, "close", "close-before-receipt").status_code == 200
    closed = sync()
    assert closed["bucket"] == "closed"
    assert (
        pilot.crm.post(
            f"/v1/conversations/{BUSINESS}/{PHONE}/read",
            json={"read": True},
            headers=_jwt(pilot.settings),
        ).status_code
        == 200
    )
    for status in ("delivered", "read", "sent", "failed"):
        encoded, headers = _signed(_receipt(provider_message, status))
        assert (
            pilot.bot.post("/webhook", content=encoded, headers=headers).status_code
            == 200
        )
    assert _command_status(pilot, "closed-receipt-reply")["status"] == "read"
    after = sync()
    assert after["bucket"] == "closed" and after["read"]
    assert after["last_message_id"] == closed["last_message_id"]
