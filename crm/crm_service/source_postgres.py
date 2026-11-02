"""Fail-closed reader for isolated synthetic PostgreSQL source facades.

No service-role credential, Supabase RPC, HTTP write or caller-provided SQL.
The SQL proposal deliberately cannot be applied to existing source databases.
Remote/production sources remain unavailable until a separate access review.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url

from crm_service.budget import ReadBudget
from crm_service.settings import Business
from crm_service.source import FIELDS, REQUIRED, SourceFailure

SAFE_TYPES = {
    "text",
    "varchar",
    "bpchar",
    "bool",
    "int2",
    "int4",
    "int8",
    "timestamptz",
    "timestamp",
    "date",
    "json",
    "jsonb",
    "numeric",
    "float4",
    "float8",
    "uuid",
}

AUDIT_SQL = """
WITH me AS (SELECT oid, rolsuper, rolcreatedb, rolcreaterole, rolinherit,
                   rolreplication, rolbypassrls FROM pg_roles WHERE rolname = current_user)
SELECT current_user AS login,
  (SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls
   FROM me) AS unsafe_role,
  EXISTS (SELECT 1 FROM pg_auth_members WHERE member = (SELECT oid FROM me)) AS memberships,
  has_database_privilege(current_database(), 'CREATE,TEMPORARY') AS database_ddl,
  EXISTS (SELECT 1 FROM pg_namespace WHERE has_schema_privilege(oid, 'CREATE')) AS schema_ddl,
  EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
            AND c.relkind IN ('r','p','v','m','f') AND
              (c.relowner = (SELECT oid FROM me)
               OR has_table_privilege(c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
               OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0
                           AND NOT a.attisdropped AND has_column_privilege(c.oid, a.attnum, 'INSERT,UPDATE,REFERENCES'))))
     AS writable_relations,
  EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE n.nspname <> 'pg_catalog' AND CASE WHEN c.relkind = 'S'
                THEN has_sequence_privilege(c.oid, 'USAGE,UPDATE,SELECT') ELSE false END) AS sequences,
  EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
            AND c.relkind IN ('r','p','v','m','f') AND
                NOT (n.nspname = 'public' AND c.relname = ANY(%s))
            AND has_any_column_privilege(c.oid, 'SELECT')) AS excessive_relations,
  EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
          WHERE (p.provolatile = 'v' OR n.nspname <> 'pg_catalog' OR p.oid >= 16384)
            AND has_function_privilege(p.oid, 'EXECUTE')) AS executable_hazards,
  (SELECT coalesce(jsonb_agg(jsonb_build_object(
       'name', c.relname, 'kind', c.relkind, 'rls', c.relrowsecurity, 'force_rls', c.relforcerowsecurity,
       'inherited', EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid = c.oid OR inhparent = c.oid),
       'columns', (SELECT coalesce(jsonb_agg(jsonb_build_object(
          'name', a.attname, 'readable', has_column_privilege(c.oid, a.attnum, 'SELECT'),
          'type_name', t.typname, 'type_namespace', tn.nspname, 'type_kind', t.typtype)), '[]'::jsonb)
          FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid
          JOIN pg_namespace tn ON tn.oid = t.typnamespace
          WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped))), '[]'::jsonb)
   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public' AND c.relname = ANY(%s)) AS source_tables
"""


class PostgresSourceAdapter:
    """One lazy connection, audited ACLs and fixed parameterized SELECT statements."""

    def __init__(self, business: Business, budget: ReadBudget | None = None):
        self.business = business
        self.budget = budget
        self.warnings: set[str] = set()
        self.columns: dict[str, list[str]] = {}
        self.connection: psycopg.Connection[dict[str, Any]] | None = None
        try:
            url = make_url(business.readonly_database_url)
        except Exception:
            raise SourceFailure("readonly_source_invalid_url") from None
        if url.drivername not in {"postgresql", "postgresql+psycopg"}:
            raise SourceFailure("readonly_source_invalid_driver")
        if url.host not in {"localhost", "127.0.0.1", "::1"} or not (url.database or "").startswith(
            ("crm_test_source_", "crm_staging_source_")
        ):
            raise SourceFailure("production_source_activation_blocked")
        if not (url.username or "").startswith("crm_staging_reader_") or url.query:
            raise SourceFailure("readonly_source_dedicated_login_required")
        self.connection_params: dict[str, Any] = {
            "host": url.host,
            "port": url.port or 5432,
            "dbname": url.database,
            "user": url.username,
            "password": url.password,
        }

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _execute(self, query: Any, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        assert self.connection is not None
        try:
            if self.budget:
                with self.budget.request():
                    milliseconds = max(1, int(min(5.0, self.budget.timeout_seconds()) * 1000))
                    self.connection.execute(
                        sql.SQL("SET statement_timeout = {}").format(sql.Literal(milliseconds))
                    )
                with self.budget.request():
                    rows = self.connection.execute(query, parameters).fetchall()
                    self.budget.record_rows(len(rows))
                return rows
            return self.connection.execute(query, parameters).fetchall()
        except psycopg.Error:
            raise SourceFailure("readonly_source_query_denied_or_failed") from None

    def _open(self) -> None:
        if self.connection is not None:
            return
        timeout = min(5.0, self.budget.timeout_seconds()) if self.budget else 5.0
        try:
            self.connection = psycopg.connect(
                **self.connection_params,
                row_factory=dict_row,
                autocommit=True,
                connect_timeout=max(1, int(timeout)),
                options=f"-c statement_timeout={max(1, int(timeout * 1000))} -c lock_timeout=1000",
            )
            if self.budget:
                self.budget.connections_peak = 1
            self._audit()
        except (psycopg.Error, SourceFailure):
            self.close()
            raise SourceFailure("readonly_source_acl_verification_failed") from None

    def _audit(self) -> None:
        audit = self._execute(AUDIT_SQL, (list(FIELDS), list(FIELDS)))[0]
        flags = (
            "unsafe_role",
            "memberships",
            "database_ddl",
            "schema_ddl",
            "writable_relations",
            "sequences",
            "excessive_relations",
            "executable_hazards",
        )
        if any(audit.get(flag) for flag in flags):
            raise SourceFailure("readonly_source_acl_verification_failed")
        tables = {row["name"]: row for row in audit["source_tables"]}
        for name, fields in FIELDS.items():
            table = tables.get(name)
            if not table:
                self.warnings.add(f"missing_table:{name}")
                if name == "message_logs":
                    raise SourceFailure("missing_message_logs")
                continue
            if table["kind"] != "r" or not table["rls"] or not table["force_rls"] or table["inherited"]:
                raise SourceFailure("readonly_source_rls_or_relation_unsafe")
            columns = {column["name"]: column["readable"] for column in table["columns"]}
            for column in table["columns"]:
                if column["name"] in fields and (
                    column["type_namespace"] != "pg_catalog"
                    or column["type_name"] not in SAFE_TYPES
                    or column["type_kind"] != "b"
                ):
                    raise SourceFailure("readonly_source_custom_type_forbidden")
            if any(readable and column not in fields for column, readable in columns.items()):
                raise SourceFailure("readonly_source_excessive_column_grant")
            available = []
            for column in fields:
                if column not in columns:
                    self.warnings.add(f"missing_column:{name}.{column}")
                    if column in REQUIRED[name]:
                        raise SourceFailure("readonly_source_identity_column_missing")
                elif not columns[column]:
                    raise SourceFailure("readonly_source_select_grant_missing")
                else:
                    available.append(column)
            self.columns[name] = available

    def page(self, table: str, *, after: str | int | None, limit: int) -> list[dict[str, Any]]:
        if table not in FIELDS or not 1 <= limit <= 1000:
            raise SourceFailure("readonly_source_table_or_limit_forbidden")
        self._open()
        columns = self.columns.get(table)
        if not columns:
            return []
        key = "id" if table == "message_logs" else "phone_number"
        filters: list[Any] = []
        parameters: list[Any] = []
        if after is not None:
            filters.append(sql.SQL("{} > %s").format(sql.Identifier(key)))
            parameters.append(after)
        if table == "handoff_deliveries":
            filters.append(sql.SQL("business_id = %s"))
            parameters.append(self.business.id)
        query = sql.SQL("SELECT {} FROM public.{}{}").format(
            sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.Identifier(table),
            sql.SQL(" WHERE ") + sql.SQL(" AND ").join(filters) if filters else sql.SQL(""),
        )
        query += sql.SQL(" ORDER BY {} ASC LIMIT %s").format(sql.Identifier(key))
        parameters.append(limit)
        rows = self._execute(query, tuple(parameters))
        # Keep the existing mirror's JSON document contract; no driver objects leak into it.
        return json.loads(json.dumps(rows, default=_json_value))


def _json_value(value: Any) -> str | int | float:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    raise TypeError("Unsupported source value")
