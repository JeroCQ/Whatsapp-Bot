import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_existing_tanaka_upgrade_covers_current_runtime_schema():
    sql = (ROOT / "supabase" / "upgrade_existing_tanaka.sql").read_text(encoding="utf-8").lower()

    assert "add column if not exists follow_up_token" in sql
    assert "add column if not exists customer_data" in sql
    assert "add column if not exists order_summary" in sql
    assert "uq_conversation_states_active_chatwoot_conversation_id" in sql
    assert "idx_message_logs_phone_created_at" in sql
    assert "values ('catalogos', 'catalogos', true)" in sql


def test_existing_tanaka_upgrade_is_non_destructive():
    sql = (ROOT / "supabase" / "upgrade_existing_tanaka.sql").read_text(encoding="utf-8").lower()

    assert "drop table" not in sql
    assert "truncate " not in sql
    assert "delete from" not in sql


def test_new_project_bootstrap_includes_all_incremental_tanaka_features():
    sql = (ROOT / "supabase" / "bootstrap.sql").read_text(encoding="utf-8").lower()

    for required in (
        "processed_webhook_events",
        "add column if not exists chatwoot_conversation_id",
        "add column if not exists follow_up_token",
        "add column if not exists customer_data",
        "add column if not exists order_summary",
        "uq_conversation_states_active_chatwoot_conversation_id",
        "idx_message_logs_phone_created_at",
        "values ('catalogos', 'catalogos', true, 209715200)",
        "file_size_limit = greatest",
        "content_type text",
        "size_bytes bigint",
        "length(trim(description)) between 1 and 2000",
        "create table if not exists public.dashboard_admins",
        "enable row level security",
        "revoke all on table public.dashboard_admins from anon, authenticated",
    ):
        assert required in sql

    assert "do not run upgrade_existing_tanaka.sql afterward" in sql


def test_message_log_roles_support_runtime_markers_and_chatwoot_history():
    bootstrap = (ROOT / "supabase" / "bootstrap.sql").read_text(encoding="utf-8").lower()
    repair = (ROOT / "supabase" / "enable_runtime_message_roles.sql").read_text(encoding="utf-8").lower()
    expected = "role in ('user', 'model', 'system', 'asesor')"

    assert expected in bootstrap
    assert expected in repair
    assert "drop constraint if exists message_logs_role_check" in repair
    assert "canonical equivalent of enable_runtime_message_roles.sql" in bootstrap
    assert "canonical supabase/bootstrap.sql" in repair


def test_generic_existing_brand_upgrade_has_current_runtime_parity():
    sql = (ROOT / "supabase" / "upgrade_existing_brand.sql").read_text(encoding="utf-8").lower()

    for required in (
        "add column if not exists chatwoot_conversation_id",
        "add column if not exists follow_up_token",
        "add column if not exists customer_data",
        "add column if not exists order_summary",
        "create table if not exists public.processed_webhook_events",
        "duplicate chatwoot_conversation_id values must be resolved before upgrading",
        "role in ('user', 'model', 'system', 'asesor')",
        "uq_conversation_states_active_chatwoot_conversation_id",
        "create table if not exists public.dashboard_admins",
        "enable row level security",
        "values ('catalogos', 'catalogos', true, 209715200)",
        "file_size_limit = greatest",
        "add column if not exists content_type",
        "add column if not exists size_bytes",
        "drop constraint if exists catalog_assets_description_check",
        "length(trim(description)) between 1 and 2000",
    ):
        assert required in sql


def test_generic_existing_brand_upgrade_is_non_destructive():
    sql = (ROOT / "supabase" / "upgrade_existing_brand.sql").read_text(encoding="utf-8").lower()

    assert "drop table" not in sql
    assert "truncate " not in sql
    assert "delete from" not in sql


@pytest.fixture(scope="module")
def isolated_postgres():
    """Exercise real migrations without using any configured application DB.

    Install local PostgreSQL server/client binaries or set POSTGRES_BIN_DIR to
    run these integration checks. The temporary server accepts Unix sockets only.
    """
    bin_dir = os.getenv("POSTGRES_BIN_DIR")
    binaries = {
        name: str(Path(bin_dir) / name) if bin_dir else shutil.which(name)
        for name in ("initdb", "pg_ctl", "psql")
    }
    if any(not value or not Path(value).is_file() for value in binaries.values()):
        pytest.skip("PostgreSQL integration checks need initdb, pg_ctl and psql (or POSTGRES_BIN_DIR)")
    if os.geteuid() == 0:
        pytest.skip("PostgreSQL initdb must run as an unprivileged user")

    with tempfile.TemporaryDirectory(prefix="whatsapp-identity-pg-") as directory:
        root = Path(directory)
        data = root / "data"
        subprocess.run(
            [binaries["initdb"], "-D", str(data), "-U", "identity_test",
             "--auth=trust", "--no-locale", "--encoding=UTF8"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            [binaries["pg_ctl"], "-D", str(data), "-l", str(root / "server.log"),
             "-o", f"-F -c listen_addresses='' -k {root} -p 55441",
             "-w", "-t", "30", "start"],
            check=True, capture_output=True, text=True,
        )

        def execute(sql, database="postgres", *, succeeds=True):
            result = subprocess.run(
                [binaries["psql"], "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1",
                 "-h", str(root), "-p", "55441", "-U", "identity_test", "-d", database],
                input=sql, capture_output=True, text=True,
            )
            if succeeds:
                assert result.returncode == 0, result.stderr
                return result.stdout.strip()
            assert result.returncode != 0, "Invalid data unexpectedly bypassed a database constraint"
            return result.stderr

        try:
            execute("CREATE ROLE anon; CREATE ROLE authenticated;")
            yield execute
        finally:
            subprocess.run(
                [binaries["pg_ctl"], "-D", str(data), "-m", "immediate", "-w", "stop"],
                check=True, capture_output=True, text=True,
            )


LEGACY_IDENTITY_SCHEMA = """
CREATE TYPE public.bot_state AS ENUM ('GREETING', 'HUMAN_HANDOFF');
CREATE TABLE public.customers (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  phone_number varchar(25) UNIQUE NOT NULL,
  name varchar(100),
  created_at timestamptz DEFAULT now()
);
CREATE TABLE public.conversation_states (
  phone_number varchar(25) PRIMARY KEY REFERENCES public.customers(phone_number),
  current_state public.bot_state DEFAULT 'GREETING',
  is_paused boolean DEFAULT false,
  chatwoot_conversation_id integer,
  follow_up_token varchar,
  customer_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  order_summary text
);
CREATE TABLE public.message_logs (
  id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  phone_number varchar(25) NOT NULL REFERENCES public.customers(phone_number),
  role varchar(20) NOT NULL CHECK (role IN ('user', 'model')),
  content text NOT NULL DEFAULT '',
  created_at timestamptz DEFAULT now()
);
INSERT INTO public.customers (phone_number, name) VALUES ('573025991292', 'Existing customer');
INSERT INTO public.conversation_states (
  phone_number, is_paused, chatwoot_conversation_id, follow_up_token, customer_data, order_summary
) VALUES ('573025991292', true, 42, 'existing-token', '{"Ciudad":"Bogota"}', 'Existing order');
INSERT INTO public.message_logs (phone_number, role, content)
VALUES ('573025991292', 'user', 'Keep the existing conversation');
"""


def _identity_data_snapshot(execute, database):
    return json.loads(execute("""
      SELECT json_build_object(
        'customers', (SELECT json_agg(t ORDER BY t.phone_number) FROM public.customers t),
        'states', (SELECT json_agg(t ORDER BY t.phone_number) FROM public.conversation_states t),
        'logs', (SELECT json_agg(t ORDER BY t.id) FROM public.message_logs t)
      );
    """, database))


@pytest.mark.parametrize("scenario", ["fresh", "existing_upgrade", "interrupted_bootstrap"])
def test_whatsapp_identity_migration_preserves_data_and_constraints(isolated_postgres, scenario):
    """Fresh installs and old databases retain exact opaque IDs and relational integrity."""
    execute = isolated_postgres
    database = f"identity_{scenario}"
    execute(f"CREATE DATABASE {database};")
    # Minimal stand-ins for Supabase-managed objects referenced by the complete
    # canonical scripts. No external service or production credential is used.
    execute("""
      CREATE SCHEMA auth;
      CREATE TABLE auth.users (id uuid PRIMARY KEY);
      CREATE SCHEMA storage;
      CREATE TABLE storage.buckets (
        id text PRIMARY KEY, name text, public boolean, file_size_limit bigint
      );
    """, database)

    legacy = None
    if scenario != "fresh":
        execute(LEGACY_IDENTITY_SCHEMA, database)
        legacy = _identity_data_snapshot(execute, database)
        # Prove that this fixture reproduces the original deployment failure.
        error = execute(
            "INSERT INTO public.customers (phone_number) VALUES ('CO.ENT.' || repeat('A', 128));",
            database, succeeds=False,
        )
        assert "value too long for type character varying(25)" in error

    script_name = "upgrade_existing_brand.sql" if scenario == "existing_upgrade" else "bootstrap.sql"
    sql = (ROOT / "supabase" / script_name).read_text(encoding="utf-8")
    execute(sql, database)
    if legacy:
        # A bootstrap rerun adds some columns to the partial schema. Compare all
        # previously populated fields while allowing those additive defaults.
        after = _identity_data_snapshot(execute, database)
        for table, rows in legacy.items():
            for old_row, new_row in zip(rows, after[table], strict=True):
                assert {key: new_row[key] for key in old_row} == old_row

    assert execute("""
      SELECT count(*) FROM information_schema.columns
      WHERE table_schema = 'public'
        AND table_name IN ('customers', 'conversation_states', 'message_logs')
        AND column_name = 'phone_number' AND data_type = 'text';
    """, database) == "3"

    identities = ["573001234567", "CO.ENT." + "A" * 128, "US.AbCdEf123", "US.aBcDeF123"]
    for identity in identities:
        execute(f"""
          INSERT INTO public.customers (phone_number, name) VALUES ('{identity}', 'New customer');
          INSERT INTO public.conversation_states (phone_number) VALUES ('{identity}');
          INSERT INTO public.message_logs (phone_number, role, content)
          VALUES ('{identity}', 'system', 'Identity survived end to end');
        """, database)
    assert json.loads(execute("""
      SELECT json_agg(c.phone_number ORDER BY c.phone_number)
      FROM public.customers c
      JOIN public.conversation_states s USING (phone_number)
      JOIN public.message_logs l USING (phone_number)
      WHERE l.content = 'Identity survived end to end';
    """, database)) == sorted(identities)

    before_rerun = _identity_data_snapshot(execute, database)
    execute(sql, database)
    assert _identity_data_snapshot(execute, database) == before_rerun

    for statement in (
        "INSERT INTO public.customers (phone_number) VALUES ('573001234567');",
        "INSERT INTO public.conversation_states (phone_number) VALUES ('573001234567');",
    ):
        assert "duplicate key value violates unique constraint" in execute(statement, database, succeeds=False)
    for statement in (
        "INSERT INTO public.conversation_states (phone_number) VALUES ('CO.Unstored');",
        "INSERT INTO public.message_logs (phone_number, role) VALUES ('CO.Unstored', 'user');",
        "DELETE FROM public.customers WHERE phone_number = '573001234567';",
    ):
        assert "violates foreign key constraint" in execute(statement, database, succeeds=False)
