"""Local-only synthetic staging loader and API smoke procedure.

This module is deliberately separate from the worker. It can write fixtures only
to explicitly marked, loopback PostgreSQL databases with disposable names.
"""

import argparse
import hashlib
import json
import os
import secrets
import shlex
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import URL, make_url

from alembic import command
from alembic.config import Config
from crm_service.api import create_app
from crm_service.db import database
from crm_service.models import Base, NotificationEvent, SyncStatus
from crm_service.settings import Settings
from crm_service.sync import Synchronizer

MARKER = "crm-synthetic-only-v1"
BUSINESSES = ("synthetic_one", "synthetic_two")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
TABLES = {"customers", "conversation_states", "message_logs", "handoff_deliveries"}
FIXED_TIME = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


def disposable_url(value: str, *, source: bool = False) -> URL:
    """Reject host overrides, remote hosts, tunnels to remote servers and normal DB names."""
    parsed = make_url(value)
    prefixes = ("crm_test_source_", "crm_staging_source_") if source else ("crm_test", "crm_staging_")
    if (
        parsed.drivername != "postgresql+psycopg"
        or parsed.host not in LOOPBACK
        or not parsed.database
        or not parsed.database.startswith(prefixes)
        or parsed.query
        or not parsed.username
    ):
        raise ValueError("Synthetic staging requires an explicit loopback disposable PostgreSQL database")
    return parsed


def local_engine(value: str, *, source: bool = False) -> Engine:
    parsed = disposable_url(value, source=source)
    engine = create_engine(parsed, hide_parameters=True, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            server = connection.scalar(text("SELECT host(inet_server_addr())"))
            if server not in {"127.0.0.1", "::1"}:
                raise ValueError("Synthetic staging refuses a non-loopback PostgreSQL server")
    except Exception:
        engine.dispose()
        raise
    return engine


def assert_notifications_disabled() -> None:
    if os.environ.get("NOTIFICATIONS_ENABLED", "false").lower() != "false":
        raise ValueError("Synthetic staging requires NOTIFICATIONS_ENABLED=false")


def mark_empty_database(engine: Engine, *, source: bool) -> None:
    marker_table = "crm_synthetic_source_marker" if source else "crm_synthetic_staging_marker"
    schema = "public" if source else "crm_synthetic_fixture"
    with engine.begin() as connection:
        schemas = connection.scalars(
            text(
                "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg\\_%' ESCAPE '\\' AND nspname <> 'information_schema'"
            )
        )
        if set(schemas) - ({"public"} if source else {"public", "crm_synthetic_fixture"}):
            raise ValueError("Refusing a database with schemas outside the synthetic fixture")
        existing = set(
            connection.scalars(
                text(
                    "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f','S')"
                )
            )
        )
        allowed = TABLES | {marker_table} if source else set(Base.metadata.tables) | {"alembic_version"}
        if existing - allowed:
            raise ValueError("Refusing a database with tables outside the synthetic fixture")
        if connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT LIKE 'pg\\_%' ESCAPE '\\' AND n.nspname <> 'information_schema')"
            )
        ):
            raise ValueError("Refusing a database with routines outside the synthetic fixture")
        if not inspect(connection).has_table(marker_table, schema=schema):
            if existing:
                raise ValueError("Refusing an existing database without the synthetic ownership marker")
            if not source:
                connection.execute(text("CREATE SCHEMA crm_synthetic_fixture"))
            connection.execute(text(f"CREATE TABLE {schema}.{marker_table} (marker text PRIMARY KEY)"))
            connection.execute(
                text(f"INSERT INTO {schema}.{marker_table} VALUES (:marker)"), {"marker": MARKER}
            )
        else:
            values = connection.scalars(text(f"SELECT marker FROM {schema}.{marker_table}")).all()
            if values != [MARKER]:
                raise ValueError("Synthetic ownership marker is invalid")


def fixture_rows(business: str) -> dict[str, list[dict[str, Any]]]:
    if business not in BUSINESSES:
        raise ValueError("Only the two explicit synthetic businesses may be seeded")
    names = (
        ("573000000001", "pending"),
        ("573000000002", "responded"),
        ("573000000003", "closed"),
        ("573000000004", "reopened"),
    )
    customers = [
        {"phone_number": phone, "name": f"SYNTHETIC {business} {kind}", "created_at": FIXED_TIME}
        for phone, kind in names
    ]
    states = []
    for phone, kind in names:
        states.append(
            {
                "phone_number": phone,
                "is_paused": kind in {"pending", "responded"},
                "current_state": "HUMAN_HANDOFF" if kind in {"pending", "responded"} else "GREETING",
                "handoff_reason": "SYNTHETIC fixture only",
                "last_interaction": FIXED_TIME,
                "chatwoot_conversation_id": 202 if kind == "responded" else None,
                "customer_data": {},
                "order_summary": "SYNTHETIC",
            }
        )
    messages = []

    def message(identity: int, phone: str, role: str, content: str, refs: list[str] | None = None) -> None:
        metadata: dict[str, Any] = {"business_id": business}
        if role == "user":
            metadata["source_event_id"] = f"{business}:{identity}"
        if refs is not None:
            metadata["inbound_message_ids"] = refs
        messages.append(
            {
                "id": identity,
                "phone_number": phone,
                "role": role,
                "content": content,
                "created_at": FIXED_TIME + timedelta(seconds=identity),
                "metadata": metadata,
                "event_key": f"{business}:{identity}",
            }
        )

    message(101, names[0][0], "user", "SYNTHETIC pending question")
    message(102, names[0][0], "system", "HANDOFF: SYNTHETIC pending")
    message(201, names[1][0], "user", "SYNTHETIC answered question")
    message(202, names[1][0], "system", "HANDOFF: SYNTHETIC created")
    message(203, names[1][0], "asesor", "SYNTHETIC advisor response", [f"{business}:201"])
    message(301, names[2][0], "user", "SYNTHETIC closed question")
    message(302, names[2][0], "system", "RESOLVED: SYNTHETIC closed")
    message(401, names[3][0], "system", "HANDOFF: SYNTHETIC old cycle")
    message(402, names[3][0], "asesor", "SYNTHETIC old advisor")
    message(403, names[3][0], "system", "RESOLVED: SYNTHETIC old cycle")
    message(404, names[3][0], "user", "SYNTHETIC reopened question")
    deliveries = [
        {
            "business_id": business,
            "phone_number": names[0][0],
            "request_id": "synthetic-pending",
            "status": "pending",
            "attempts": 0,
            "retry_at": FIXED_TIME,
            "conversation_id": None,
            "last_error": None,
            "created_at": FIXED_TIME,
            "updated_at": FIXED_TIME,
        },
        {
            "business_id": business,
            "phone_number": names[1][0],
            "request_id": "synthetic-created",
            "status": "created",
            "attempts": 1,
            "retry_at": FIXED_TIME,
            "conversation_id": 202,
            "last_error": None,
            "created_at": FIXED_TIME + timedelta(seconds=200),
            "updated_at": FIXED_TIME + timedelta(seconds=200),
        },
    ]
    return {
        "customers": customers,
        "conversation_states": states,
        "message_logs": messages,
        "handoff_deliveries": deliveries,
    }


SOURCE_DDL = """
CREATE TABLE IF NOT EXISTS public.customers (
    phone_number text PRIMARY KEY, name text, created_at timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS public.conversation_states (
    phone_number text PRIMARY KEY, current_state text, is_paused boolean,
    handoff_reason text, last_interaction timestamptz, chatwoot_conversation_id bigint,
    customer_data jsonb, order_summary text);
CREATE TABLE IF NOT EXISTS public.message_logs (
    id bigint PRIMARY KEY, phone_number text NOT NULL, role text NOT NULL,
    content text NOT NULL, created_at timestamptz NOT NULL, metadata jsonb, event_key text UNIQUE);
CREATE TABLE IF NOT EXISTS public.handoff_deliveries (
    business_id text NOT NULL, phone_number text NOT NULL, request_id text,
    status text, attempts integer, retry_at timestamptz, conversation_id bigint,
    last_error text, created_at timestamptz, updated_at timestamptz,
    PRIMARY KEY (business_id, phone_number));
"""


def seed_source(admin_url: str, business: str, proposal: Path) -> str:
    engine = local_engine(admin_url, source=True)
    try:
        mark_empty_database(engine, source=True)
        rows = fixture_rows(business)
        with engine.begin() as connection:
            connection.exec_driver_sql(SOURCE_DDL)
            for table in ("handoff_deliveries", "message_logs", "conversation_states", "customers"):
                connection.execute(text(f"DELETE FROM public.{table}"))
            for table, items in rows.items():
                for item in items:
                    columns = list(item)
                    values = {
                        name: json.dumps(value) if isinstance(value, dict) else value
                        for name, value in item.items()
                    }
                    expressions = [
                        f"CAST(:{name} AS jsonb)" if isinstance(item[name], dict) else f":{name}"
                        for name in columns
                    ]
                    connection.execute(
                        text(
                            f"INSERT INTO public.{table} ({','.join(columns)}) "
                            f"VALUES ({','.join(expressions)})"
                        ),
                        values,
                    )
        database_name = make_url(admin_url).database
        role = "crm_staging_reader_" + hashlib.sha256(str(database_name).encode()).hexdigest()[:20]
        password = secrets.token_urlsafe(36)
        raw = engine.raw_connection()
        try:
            driver = raw.driver_connection
            assert driver is not None
            driver.autocommit = True
            with driver.cursor() as cursor:
                cursor.execute("SELECT set_config('crm.reader_role', %s, false)", (role,))
                cursor.execute(proposal.read_text())
                cursor.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password))
                )
        finally:
            raw.close()
        return make_url(admin_url).set(username=role, password=password).render_as_string(hide_password=False)
    finally:
        engine.dispose()


def migrate_crm(url: str) -> None:
    engine = local_engine(url)
    try:
        mark_empty_database(engine, source=False)
        old = os.environ.get("CRM_DATABASE_URL")
        os.environ["CRM_DATABASE_URL"] = url
        try:
            command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")
        finally:
            if old is None:
                os.environ.pop("CRM_DATABASE_URL", None)
            else:
                os.environ["CRM_DATABASE_URL"] = old
    finally:
        engine.dispose()


def write_config(output: Path, crm_url: str, sources: dict[str, str]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("api.env", "worker.env")):
        raise ValueError("Refusing to overwrite existing synthetic configuration")
    common = {
        "CRM_DATABASE_URL": crm_url,
        "CRM_BUSINESSES": ",".join(BUSINESSES),
        "CRM_JWT_SECRET": secrets.token_urlsafe(48),
        "CRM_SYNC_SECRET": secrets.token_urlsafe(48),
        "CRM_SOURCE_MODE": "synthetic",
        "NOTIFICATIONS_ENABLED": "false",
    }
    for filename, values in (
        ("api.env", common),
        (
            "worker.env",
            {
                **common,
                **{
                    f"{business.upper()}_SOURCE_READONLY_DATABASE_URL": value
                    for business, value in sources.items()
                },
            },
        ),
    ):
        path = output / filename
        with path.open("x") as target:
            path.chmod(0o600)
            target.write("".join(f"{name}={shlex.quote(value)}\n" for name, value in values.items()))


def load_staging_settings(require_sources: bool = True) -> Settings:
    assert_notifications_disabled()
    settings = Settings.from_env(service="worker" if require_sources else "api")
    disposable_url(settings.database_url)
    if set(settings.businesses) != set(BUSINESSES) or settings.notifications_enabled:
        raise ValueError(
            "Synthetic staging requires exactly the two fixture businesses and notifications disabled"
        )
    if require_sources:
        for business in settings.businesses.values():
            disposable_url(business.readonly_database_url, source=True)
    return settings


def require_marked_crm(value: str) -> None:
    engine = local_engine(value)
    try:
        with engine.connect() as connection:
            if not inspect(connection).has_table(
                "crm_synthetic_staging_marker", schema="crm_synthetic_fixture"
            ):
                raise ValueError("Synthetic CRM ownership marker is required")
            values = connection.scalars(
                text("SELECT marker FROM crm_synthetic_fixture.crm_synthetic_staging_marker")
            ).all()
            if values != [MARKER]:
                raise ValueError("Synthetic CRM ownership marker is invalid")
    finally:
        engine.dispose()


def synchronize(settings: Settings) -> dict[str, str]:
    require_marked_crm(settings.database_url)
    engine, sessions = database(settings)
    try:
        results: dict[str, str] = {}
        pending = set(BUSINESSES)
        worker = Synchronizer(settings, sessions)
        for _ in range(100):
            for business_id in sorted(pending):
                result = worker.run(business_id)[business_id]
                results[business_id] = result
                if result == "ok":
                    pending.remove(business_id)
            if any(result in {"failed", "backoff"} for result in results.values()):
                raise RuntimeError(
                    "Synthetic synchronization failed or is in backoff; inspect per-business status"
                )
            with sessions() as session:
                if not pending and all(
                    (row := session.get(SyncStatus, name)) and row.baseline_complete for name in BUSINESSES
                ):
                    return results
        raise RuntimeError("Synthetic baseline did not finish within 100 bounded executions")
    finally:
        engine.dispose()


def verify_api(settings: Settings) -> dict[str, Any]:
    """Exercise the actual FastAPI routes in-process against the disposable CRM PG database."""
    if not __debug__:
        raise ValueError("Synthetic verification must run without Python optimization")
    if settings.notifications_enabled:
        raise ValueError("Notifications must be disabled for synthetic validation")
    disposable_url(settings.database_url)
    require_marked_crm(settings.database_url)
    profile = "synthetic.employee.one"
    issued = int(time.time())

    def auth(businesses: list[str]) -> dict[str, str]:
        claims = {
            "iss": "chatty-catalog-compass",
            "aud": "whatsapp-crm",
            "profile": profile,
            "allowed_businesses": businesses,
            "iat": issued,
            "exp": issued + 120,
        }
        encoded = jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
        token = encoded.decode() if isinstance(encoded, bytes) else encoded
        return {"Authorization": "Bearer " + token}

    api_settings = replace(
        settings,
        notifications_enabled=False,
        businesses={
            name: replace(business, source_url="", source_key="", readonly_database_url="")
            for name, business in settings.businesses.items()
        },
    )
    app = create_app(api_settings)
    try:
        with TestClient(app) as client:
            assert client.get("/v1/inbox").status_code == 401
            headers = auth(list(BUSINESSES))
            items = []
            cursor = None
            while True:
                params: dict[str, Any] = {"bucket": "all", "limit": 1}
                if cursor:
                    params["cursor"] = cursor
                response = client.get("/v1/inbox", params=params, headers=headers)
                assert response.status_code == 200
                page = response.json()
                items.extend(page["items"])
                cursor = page["next_cursor"]
                if not cursor:
                    break
            assert len(items) == 8
            identities = {(item["business_id"], item["phone_number"]) for item in items}
            assert len(identities) == 8
            expected = {
                "573000000001": "awaiting_human",
                "573000000002": "human_active",
                "573000000003": "closed",
                "573000000004": "ai_active",
            }
            assert all(item["bucket"] == expected[item["phone_number"]] for item in items)
            for business in BUSINESSES:
                names = [item["name"] for item in items if item["business_id"] == business]
                assert all(business in name for name in names)
            scoped = auth([BUSINESSES[0]])
            assert client.get(f"/v1/inbox?business_id={BUSINESSES[1]}", headers=scoped).status_code == 403
            base = f"/v1/conversations/{BUSINESSES[0]}/573000000001"
            assert client.post(base + "/read", json={"read": True}, headers=scoped).status_code == 200
            detail = client.get(base, headers=scoped).json()
            assert detail["read"] is True and detail["bucket"] == "awaiting_human"
            other = client.get(f"/v1/conversations/{BUSINESSES[1]}/573000000001", headers=headers).json()
            assert other["read"] is False
            version = detail["version"]
            assert (
                client.patch(
                    base + "/crm", json={"version": version, "stage": "synthetic_checked"}, headers=scoped
                ).status_code
                == 200
            )
            assert (
                client.patch(
                    base + "/crm", json={"version": version, "stage": "stale"}, headers=scoped
                ).status_code
                == 409
            )
            note = client.post(base + "/notes", json={"body": "SYNTHETIC test note"}, headers=scoped)
            assert note.status_code == 201
            note_url = base + "/notes/" + note.json()["id"]
            assert (
                client.patch(note_url, json={"body": "SYNTHETIC edited"}, headers=scoped).status_code == 200
            )
            assert client.get(note_url, headers=scoped).json()["body"] == "SYNTHETIC edited"
            assert client.delete(note_url, headers=scoped).status_code == 204
            task = client.post(base + "/tasks", json={"title": "SYNTHETIC test task"}, headers=scoped)
            assert task.status_code == 201
            task_url = base + "/tasks/" + task.json()["id"]
            assert client.patch(task_url, json={"completed": True}, headers=scoped).status_code == 200
            assert client.get(task_url, headers=scoped).json()["completed"] is True
            assert client.delete(task_url, headers=scoped).status_code == 204
            assert client.get("/v1/notifications", headers=headers).json()["items"] == []
        engine, sessions = database(settings)
        try:
            with sessions() as session:
                assert session.query(NotificationEvent).count() == 0
        finally:
            engine.dispose()
        return {
            "status": "verified",
            "businesses": list(BUSINESSES),
            "conversations": len(items),
            "external_messages": 0,
            "notification_events": 0,
        }
    finally:
        app.state.engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "sync", "verify"))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--proposal-sql",
        type=Path,
        default=Path(__file__).parents[1] / "security" / "isolated_source_proposal.sql",
    )
    args = parser.parse_args()
    assert_notifications_disabled()
    if args.action == "seed":
        if args.output_dir is None:
            parser.error("seed requires --output-dir outside the checkout")
        root = Path(__file__).parents[1].resolve()
        root = next((parent for parent in (root, *root.parents) if (parent / ".git").exists()), root)
        if args.output_dir.resolve().is_relative_to(root):
            parser.error("Generated secret configuration must stay outside the CRM checkout")
        if any((args.output_dir / name).exists() for name in ("api.env", "worker.env")):
            parser.error("Refusing to overwrite existing synthetic configuration")
        crm_url = os.environ["CRM_STAGING_CRM_ADMIN_URL"]
        inputs = {
            BUSINESSES[0]: os.environ["CRM_STAGING_SOURCE_ONE_ADMIN_URL"],
            BUSINESSES[1]: os.environ["CRM_STAGING_SOURCE_TWO_ADMIN_URL"],
        }
        for url in inputs.values():
            disposable_url(url, source=True)
        disposable_url(crm_url)
        if len({make_url(value).database for value in (crm_url, *inputs.values())}) != 3:
            parser.error("CRM and both synthetic sources must be three different databases")
        migrate_crm(crm_url)
        sources = {
            business: seed_source(url, business, args.proposal_sql) for business, url in inputs.items()
        }
        write_config(args.output_dir, crm_url, sources)
        print(
            json.dumps(
                {
                    "status": "seeded",
                    "businesses": list(BUSINESSES),
                    "configuration_dir": str(args.output_dir),
                }
            )
        )
    elif args.action == "sync":
        print(json.dumps(synchronize(load_staging_settings())))
    else:
        print(json.dumps(verify_api(load_staging_settings(require_sources=False))))


if __name__ == "__main__":
    main()
