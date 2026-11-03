"""Permission attacks run only in databases this fixture creates and destroys."""

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from crm_service.budget import ReadBudget
from crm_service.settings import Business
from crm_service.source import SourceFailure
from crm_service.source_postgres import PostgresSourceAdapter

PROPOSAL = Path(__file__).parents[1] / "security" / "isolated_source_proposal.sql"

DDL = """
CREATE TABLE public.crm_synthetic_source_marker(marker text);
INSERT INTO public.crm_synthetic_source_marker VALUES ('crm-synthetic-only-v1');
CREATE TABLE public.customers(phone_number text PRIMARY KEY,name text,created_at timestamptz,
                              internal_secret text DEFAULT 'synthetic-private-column');
CREATE TABLE public.conversation_states(phone_number text PRIMARY KEY,current_state text,
    is_paused boolean,handoff_reason text,last_interaction timestamptz,chatwoot_conversation_id bigint,
    customer_data jsonb,order_summary text);
CREATE TABLE public.message_logs(id bigint PRIMARY KEY,phone_number text,role text,content text,
                                created_at timestamptz,metadata jsonb,event_key text);
CREATE TABLE public.handoff_deliveries(business_id text,phone_number text PRIMARY KEY,request_id text,
    status text,attempts integer,retry_at timestamptz,conversation_id bigint,last_error text,
    created_at timestamptz,updated_at timestamptz);
CREATE TABLE public.other_business_private(secret text);
CREATE FUNCTION public.synthetic_mutator() RETURNS void LANGUAGE sql SECURITY DEFINER AS
    $$ UPDATE public.customers SET name='MUTATED' $$;
INSERT INTO public.customers(phone_number,name,created_at) VALUES
    ('same-contact','Synthetic customer','2026-11-01T00:00:00Z'),
    ('hidden-contact','Synthetic RLS row','2026-11-01T00:00:00Z');
INSERT INTO public.message_logs VALUES
    (1,'same-contact','user','Synthetic message','2026-11-01T00:00:00Z','{}','event-1');
"""


class SyntheticFixture(dict):
    def __repr__(self):
        return "<synthetic source fixture; credentials omitted>"


@pytest.fixture
def isolated_source(database_url):
    base = make_url(database_url)
    assert base.host in {"127.0.0.1", "localhost", "::1"}
    assert (base.database or "").startswith("crm_test")
    suffix = uuid4().hex[:16]
    database_name = "crm_test_source_acl_" + suffix
    role = "crm_staging_reader_acl_" + suffix
    admin_options = {
        "host": base.host,
        "port": base.port or 5432,
        "user": base.username,
        "password": base.password,
    }
    helper_roles = []
    with psycopg.connect(**admin_options, dbname="postgres", autocommit=True) as cluster:
        cluster.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    admin = psycopg.connect(**admin_options, dbname=database_name, autocommit=True)
    try:
        admin.execute(DDL)
        admin.execute(sql.SQL("SET crm.reader_role = {}").format(sql.Literal(role)))
        admin.execute(PROPOSAL.read_text())
        # Only this synthetic credential/role is changed. Never output its value.
        password = "fixture-only-" + uuid4().hex
        admin.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password))
        )
        reader_options = {
            "host": base.host,
            "port": base.port or 5432,
            "dbname": database_name,
            "user": role,
            "password": password,
        }
        dsn = base.set(username=role, password=password, database=database_name)
        business = Business(
            "synthetic", "", "", readonly_database_url=dsn.render_as_string(hide_password=False)
        )
        yield SyntheticFixture(
            admin=admin, role=role, reader_options=reader_options, business=business, helpers=helper_roles
        )
    finally:
        admin.close()
        with psycopg.connect(**admin_options, dbname="postgres", autocommit=True) as cluster:
            cluster.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))
            cluster.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
            for helper in helper_roles:
                cluster.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(helper)))


def test_effective_grants_deny_data_writes_without_readonly_transaction(isolated_source):
    adapter = PostgresSourceAdapter(isolated_source["business"])
    assert adapter.page("customers", after=None, limit=10)[0]["name"] == "Synthetic RLS row"
    adapter.close()
    with psycopg.connect(**isolated_source["reader_options"], autocommit=True) as reader:
        assert reader.execute("SHOW default_transaction_read_only").fetchone()[0] == "off"
        assert reader.execute("SELECT phone_number FROM public.customers").fetchall()
        attempts = [
            "INSERT INTO public.customers(phone_number) VALUES ('forbidden')",
            "UPDATE public.customers SET name='forbidden'",
            "DELETE FROM public.customers",
            "TRUNCATE public.customers",
            "CREATE TABLE public.forbidden(id int)",
            "CREATE TEMP TABLE forbidden(id int)",
            "SELECT internal_secret FROM public.customers",
            "SELECT public.synthetic_mutator()",
            "SELECT pg_notify('synthetic-channel','forbidden')",
        ]
        for command in attempts:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                reader.execute(command)


def test_force_rls_and_policy_are_effective(isolated_source):
    admin, role = isolated_source["admin"], isolated_source["role"]
    admin.execute("DROP POLICY crm_synthetic_reader_select ON public.customers")
    admin.execute(
        sql.SQL(
            "CREATE POLICY limited ON public.customers FOR SELECT TO {} USING (phone_number='same-contact')"
        ).format(sql.Identifier(role))
    )
    adapter = PostgresSourceAdapter(isolated_source["business"])
    rows = adapter.page("customers", after=None, limit=10)
    assert [r["phone_number"] for r in rows] == ["same-contact"]
    adapter.close()


@pytest.mark.parametrize(
    "grant",
    [
        "GRANT INSERT ON public.customers TO PUBLIC",
        "GRANT UPDATE (name) ON public.customers TO PUBLIC",
        "GRANT SELECT ON public.other_business_private TO PUBLIC",
        "GRANT EXECUTE ON FUNCTION public.synthetic_mutator() TO PUBLIC",
        "GRANT EXECUTE ON FUNCTION pg_catalog.pg_notify(text,text) TO PUBLIC",
        "GRANT CREATE ON SCHEMA public TO PUBLIC",
    ],
)
def test_shared_public_privileges_cannot_be_hidden_by_noinherit(isolated_source, grant):
    isolated_source["admin"].execute(grant)
    adapter = PostgresSourceAdapter(isolated_source["business"])
    with pytest.raises(SourceFailure, match="acl_verification_failed"):
        adapter.page("message_logs", after=0, limit=1)
    adapter.close()


def test_public_temp_privilege_blocks_activation(isolated_source):
    admin = isolated_source["admin"]
    name = admin.execute("SELECT current_database()").fetchone()[0]
    admin.execute(sql.SQL("GRANT TEMPORARY ON DATABASE {} TO PUBLIC").format(sql.Identifier(name)))
    adapter = PostgresSourceAdapter(isolated_source["business"])
    with pytest.raises(SourceFailure, match="acl_verification_failed"):
        adapter.page("message_logs", after=None, limit=1)


def test_membership_is_rejected_even_when_noinherit_hides_effective_update(isolated_source):
    admin, role = isolated_source["admin"], isolated_source["role"]
    helper = "crm_test_helper_" + uuid4().hex[:16]
    isolated_source["helpers"].append(helper)
    admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(helper)))
    admin.execute(
        sql.SQL("GRANT UPDATE(name), SELECT(phone_number) ON public.customers TO {}").format(
            sql.Identifier(helper)
        )
    )
    admin.execute(
        sql.SQL("GRANT {} TO {} WITH INHERIT FALSE, SET TRUE").format(
            sql.Identifier(helper), sql.Identifier(role)
        )
    )
    with psycopg.connect(**isolated_source["reader_options"], autocommit=True) as reader:
        assert (
            reader.execute("SELECT has_column_privilege('public.customers','name','UPDATE')").fetchone()[0]
            is False
        )
        reader.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(helper)))
        assert reader.execute("SELECT current_user").fetchone()[0] == helper
    adapter = PostgresSourceAdapter(isolated_source["business"])
    with pytest.raises(SourceFailure, match="acl_verification_failed"):
        adapter.page("customers", after=None, limit=1)


@pytest.mark.parametrize(
    "flag", ["INHERIT", "BYPASSRLS", "CREATEDB", "CREATEROLE", "REPLICATION", "SUPERUSER"]
)
def test_elevated_role_flags_are_rejected(isolated_source, flag):
    isolated_source["admin"].execute(
        sql.SQL("ALTER ROLE {} " + flag).format(sql.Identifier(isolated_source["role"]))
    )
    adapter = PostgresSourceAdapter(isolated_source["business"])
    with pytest.raises(SourceFailure, match="acl_verification_failed"):
        adapter.page("customers", after=None, limit=1)


def test_disabling_force_rls_blocks_activation(isolated_source):
    isolated_source["admin"].execute("ALTER TABLE public.customers NO FORCE ROW LEVEL SECURITY")
    adapter = PostgresSourceAdapter(isolated_source["business"])
    with pytest.raises(SourceFailure, match="acl_verification_failed"):
        adapter.page("customers", after=None, limit=1)


def test_table_owner_is_rejected_even_with_force_rls(isolated_source):
    isolated_source["admin"].execute(
        sql.SQL("ALTER TABLE public.customers OWNER TO {}").format(sql.Identifier(isolated_source["role"]))
    )
    adapter = PostgresSourceAdapter(isolated_source["business"])
    with pytest.raises(SourceFailure, match="acl_verification_failed"):
        adapter.page("customers", after=None, limit=1)


def test_rls_cannot_invoke_revoked_side_effect_function(isolated_source):
    admin, role = isolated_source["admin"], isolated_source["role"]
    admin.execute("DROP POLICY crm_synthetic_reader_select ON public.customers")
    admin.execute(
        sql.SQL(
            "CREATE POLICY bad ON public.customers FOR SELECT TO {} USING (pg_notify('synthetic','blocked') IS NULL)"
        ).format(sql.Identifier(role))
    )
    adapter = PostgresSourceAdapter(isolated_source["business"])
    with pytest.raises(SourceFailure, match="query_denied_or_failed"):
        adapter.page("customers", after=None, limit=1)
    adapter.close()


def test_stable_security_definer_policy_function_is_not_callable(isolated_source):
    admin, role = isolated_source["admin"], isolated_source["role"]
    admin.execute(
        "CREATE FUNCTION public.synthetic_stable() RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER AS $$ SELECT true $$"
    )
    admin.execute("REVOKE EXECUTE ON FUNCTION public.synthetic_stable() FROM PUBLIC")
    admin.execute("DROP POLICY crm_synthetic_reader_select ON public.customers")
    admin.execute(
        sql.SQL(
            "CREATE POLICY bad ON public.customers FOR SELECT TO {} USING (public.synthetic_stable())"
        ).format(sql.Identifier(role))
    )
    adapter = PostgresSourceAdapter(isolated_source["business"])
    with pytest.raises(SourceFailure, match="query_denied_or_failed"):
        adapter.page("customers", after=None, limit=1)
    adapter.close()


def test_public_user_aggregate_is_rejected(isolated_source):
    admin = isolated_source["admin"]
    admin.execute(
        "CREATE FUNCTION public.synthetic_step(integer,integer) RETURNS integer LANGUAGE sql AS $$ SELECT $1+$2 $$"
    )
    admin.execute("REVOKE EXECUTE ON FUNCTION public.synthetic_step(integer,integer) FROM PUBLIC")
    admin.execute(
        "CREATE AGGREGATE public.synthetic_sum(integer) (SFUNC=public.synthetic_step,STYPE=integer,INITCOND='0')"
    )
    adapter = PostgresSourceAdapter(isolated_source["business"])
    with pytest.raises(SourceFailure, match="acl_verification_failed"):
        adapter.page("customers", after=None, limit=1)


def test_custom_domain_type_is_rejected_before_data_output(isolated_source):
    admin = isolated_source["admin"]
    admin.execute("CREATE DOMAIN public.synthetic_domain AS text")
    admin.execute("ALTER TABLE public.customers ALTER COLUMN name TYPE public.synthetic_domain")
    adapter = PostgresSourceAdapter(isolated_source["business"])
    with pytest.raises(SourceFailure, match="acl_verification_failed"):
        adapter.page("customers", after=None, limit=1)


def test_optional_columns_degrade_and_cursor_pages_are_stable(isolated_source):
    isolated_source["admin"].execute("ALTER TABLE public.message_logs DROP COLUMN metadata")
    adapter = PostgresSourceAdapter(isolated_source["business"])
    assert len(adapter.page("message_logs", after=0, limit=1)) == 1
    assert adapter.page("message_logs", after=1, limit=1) == []
    assert "missing_column:message_logs.metadata" in adapter.warnings
    adapter.close()


def test_audit_and_statement_reads_consume_budget(isolated_source, settings):
    budget = ReadBudget(replace(settings, sync_max_requests=20, sync_max_seconds=10))
    adapter = PostgresSourceAdapter(isolated_source["business"], budget)
    adapter.page("message_logs", after=0, limit=1)
    assert budget.requests == 4  # SET timeout + audit, SET timeout + page.
    assert budget.connections_peak == 1
    assert budget.rows == 2
    adapter.close()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://crm_staging_reader_x:synthetic@production.example.com/crm_test_source_x",
        "postgresql+psycopg://crm_staging_reader_x:synthetic@127.0.0.1/real_source",
        "postgresql+psycopg://postgres:synthetic@127.0.0.1/crm_test_source_x",
    ],
)
def test_production_and_admin_targets_rejected_before_connect(url, monkeypatch):
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: pytest.fail("must not connect"))
    business = Business("synthetic", "", "", readonly_database_url=url)
    with pytest.raises(SourceFailure):
        PostgresSourceAdapter(business)
