"""Copy Chatty data into the existing Tanaka project without switching any writer."""

import argparse
import hashlib
import os
import re
import sys
from contextlib import ExitStack
from datetime import datetime, timezone
from urllib.parse import urlsplit

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from crm_service.central_copy_connection import CENTRAL_REF, CopyError, check_project, connect
from crm_service.models import Base

BOT_DATASETS = ("briosos", "memos", "velvet")
CRM_DATASETS = ("commercial-crm", "pilot-crm")
DATASETS = BOT_DATASETS + CRM_DATASETS
BOT_TABLES = (
    "customers", "dashboard_admins", "catalog_assets", "conversation_states",
    "processed_webhook_events", "message_logs", "handoff_deliveries",
    "crm_control_commands", "crm_send_attempts", "crm_media_assets",
)
CRM_TABLES = tuple(table.name for table in Base.metadata.sorted_tables)
CRM_CONFIG_TABLES = {
    "crm_business_drafts", "crm_shared_config_draft", "crm_business_connection_refs",
    "crm_usage_turns", "crm_model_usage_attempts", "crm_support_minutes",
}
CRM_PILOT_TABLES = {"crm_pilot_publications", "crm_pilot_switch"}
CRM_INSIGHT_TABLES = {"customer_return_analyses", "promotion_permissions", "promotion_permission_events"}
BOT_REFS = {"memos": "hhzivtplonffzolfdaho", "velvet": "fhuskvtwrmuotpenmrhu"}
CRM_SCHEMA = "chatty_crm"
STAGING = "chatty_migration_stage"
RUNS = sql.Identifier("public", "chatty_migration_runs")
CRM_REVISIONS = {"0004_delivery_receipts", "0005_central_config_usage", "0006_scoped_pilot", "0007_customer_insights", "0008_commercial_events"}


def ident(*parts):
    return sql.Identifier(*parts)


def names(columns):
    return sql.SQL(", ").join(map(ident, columns))


def begin(db, *, source=False):
    db.execute(sql.SQL("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, ") +
               sql.SQL("READ ONLY" if source else "READ WRITE"))
    for setting in ("SET LOCAL search_path = public, pg_catalog", "SET LOCAL TIME ZONE 'UTC'", "SET LOCAL DateStyle = 'ISO, YMD'",
                    "SET LOCAL IntervalStyle = 'postgres'", "SET LOCAL extra_float_digits = 3",
                    "SET LOCAL statement_timeout = '30min'", "SET LOCAL lock_timeout = '10s'"):
        db.execute(setting)


def qualified(dataset, table):
    if dataset in BOT_DATASETS:
        return "public", f"chatty_{dataset}_{table}"
    return CRM_SCHEMA, table


def staging_name(epoch, dataset, table):
    suffix = hashlib.sha256(f"{epoch}/{dataset}/{table}".encode()).hexdigest()[:24]
    return ident(STAGING, "snapshot_" + suffix)


def columns(db, schema, table):
    result = db.execute("""
        SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod),
               a.attnotnull, a.atthasdef, a.attidentity, a.attgenerated
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('r','p')
          AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
    """, (schema, table)).fetchall()
    return {row[0]: row[1:] for row in result}


def primary_key(db, schema, table):
    return [row[0] for row in db.execute("""
        SELECT a.attname
        FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON k.ord <= i.indnkeyatts
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
        WHERE n.nspname = %s AND c.relname = %s AND i.indisprimary ORDER BY k.ord
    """, (schema, table))]


def reviewed_tables(source, dataset):
    if dataset in BOT_DATASETS:
        return BOT_TABLES
    revision = source.execute("SELECT version_num FROM public.alembic_version").fetchall()
    if len(revision) != 1 or revision[0][0] not in CRM_REVISIONS:
        labels = ",".join(str(item[0])[:64] for item in revision[:3])
        raise CopyError(f"Revisión CRM no admitida: {dataset}/{labels}")
    available = {r[0] for r in source.execute("""
        SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'
    """)}
    expected = set(CRM_TABLES)
    if revision[0][0] not in {"0007_customer_insights", "0008_commercial_events"}:
        expected -= CRM_INSIGHT_TABLES
    if revision[0][0] != "0008_commercial_events":
        expected.discard("crm_commercial_events")
    if revision[0][0] in {"0004_delivery_receipts", "0005_central_config_usage"}:
        expected -= CRM_PILOT_TABLES
    if revision[0][0] == "0004_delivery_receipts":
        expected -= CRM_CONFIG_TABLES
    if available != expected:
        raise CopyError(f"Tablas CRM incompatibles: {dataset}; faltan={','.join(sorted(expected-available))}; extras={','.join(sorted(available-expected))}")
    return [table for table in CRM_TABLES if table in available]


def layout(source, target, dataset, table):
    schema, destination = qualified(dataset, table)
    original = columns(source, "public", table)
    central = columns(target, schema, destination)
    key = primary_key(source, "public", table)
    if not original or not key or key != primary_key(target, schema, destination):
        raise CopyError(f"Falta tabla o clave primaria compatible: {dataset}/{table}")
    if set(original).difference(central):
        raise CopyError(f"Hay columnas de origen sin destino: {dataset}/{table}")
    prefix = f"chatty_{dataset}_" if dataset in BOT_DATASETS else ""
    for column, current in original.items():
        dest = central[column]
        dest_type = dest[0].removeprefix(prefix) if prefix else dest[0]
        if dest_type != current[0] or current[4] or dest[4]:
            raise CopyError(f"Tipo de columna incompatible: {dataset}/{table}/{column}")
    for column in central.keys() - original.keys():
        _type, required, default, identity, generated = central[column]
        if dataset in BOT_DATASETS or (required and not (default or identity or generated)):
            raise CopyError(f"Columna adicional no compatible: {dataset}/{table}/{column}")
    return list(original), key


def stream_rows(db, relation, fields, key, sink=None):
    statement = sql.SQL("COPY (SELECT {} FROM {} ORDER BY {}) TO STDOUT").format(
        names(fields), relation, names(key))
    digest = hashlib.sha256()
    with db.cursor().copy(statement) as stream:
        for chunk in stream:
            digest.update(chunk)
            if sink is not None:
                sink.write(chunk)
    return digest.hexdigest()


def assert_business_scope(source, dataset, table, fields):
    if "business_id" not in fields:
        return
    if dataset == "commercial-crm" or (dataset == "pilot-crm" and table == "audit_events"):
        # The authorized commercial CRM snapshot is the complete existing DB,
        # including dormant historical tenant keys and global audit scopes.
        # Preserve original business keys; never turn them into active registry
        # entries or broaden JWT/operator scope. Per-bot copies remain strict.
        return
    permitted = [dataset] if dataset in BOT_DATASETS else (
        ["tanaka", "velvet", "memos", "briosos"] if dataset == "commercial-crm" else ["briosos"])
    unexpected = source.execute(sql.SQL(
        "SELECT EXISTS (SELECT 1 FROM {} WHERE business_id IS NOT NULL AND NOT (business_id = ANY(%s)))"
    ).format(ident("public", table)), (permitted,)).fetchone()[0]
    if unexpected:
        raise CopyError(f"Filas fuera del alcance: {dataset}/{table}")


def stage_table(source, target, epoch, dataset, table):
    fields, key = layout(source, target, dataset, table)
    assert_business_scope(source, dataset, table, fields)
    original, stage = ident("public", table), staging_name(epoch, dataset, table)
    target.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(stage))
    target.execute(sql.SQL("CREATE TABLE {} AS SELECT {} FROM {} WITH NO DATA").format(
        stage, names(fields), ident(*qualified(dataset, table))))
    with target.cursor().copy(sql.SQL("COPY {} ({}) FROM STDIN").format(stage, names(fields))) as sink:
        digest = stream_rows(source, original, fields, key, sink)
    count = target.execute(sql.SQL("SELECT count(*) FROM {}").format(stage)).fetchone()[0]
    if digest != stream_rows(target, stage, fields, key):
        raise CopyError(f"La copia no conserva las filas exactas: {dataset}/{table}")
    return {"columns": fields, "key": key, "rows": count, "sha256": digest}


def sequences(db, schema):
    return db.execute("""
        SELECT sequencename, increment_by, max_value, cycle FROM pg_sequences
        WHERE schemaname = %s ORDER BY sequencename
    """, (schema,))


def sequence_next(db, schema, name, increment, maximum, cycle):
    last, called = db.execute(sql.SQL("SELECT last_value, is_called FROM {}").format(
        ident(schema, name))).fetchone()
    next_value = last + (increment if called else 0)
    if cycle or increment <= 0 or next_value > maximum:
        raise CopyError("Secuencia cíclica, agotada o incompatible.")
    return next_value


def stage_sequences(source, target, dataset):
    if dataset in CRM_DATASETS:
        options = [(row, row[0]) for row in sequences(source, "public")]
        schema = CRM_SCHEMA
    else:
        by_name = {row[0]: row for row in sequences(source, "public")}
        options = []
        schema = "public"
        # Resolve the actual sequence, including identity sequences created by PostgreSQL.
        table = ident(*qualified(dataset, "message_logs"))
        source_name = source.execute(
            "SELECT pg_get_serial_sequence('public.message_logs', 'id')").fetchone()[0]
        target_name = target.execute(
            "SELECT pg_get_serial_sequence(%s, 'id')", (table.as_string(target),)).fetchone()[0]
        if not source_name or not target_name:
            # Older clones can use an explicitly named, unowned nextval sequence.
            source_name = source_name or "public.message_logs_id_seq"
            target_name = target_name or f"public.chatty_{dataset}_message_logs_id_seq"
        seq = source_name.split(".", 1)[1].strip('"')
        if seq not in by_name:
            raise CopyError(f"Secuencia de mensajes no localizada: {dataset}")
        options.append((by_name[seq], target_name.split(".", 1)[1].strip('"')))
    target_limits = {row[0]: row[1:] for row in sequences(target, schema)}
    result = {}
    for (name, increment, maximum, cycle), destination in options:
        if target_limits.get(destination) != (increment, maximum, cycle):
            raise CopyError(f"Secuencia destino incompatible: {dataset}/{destination}")
        result[destination] = {
            "next": str(sequence_next(source, "public", name, increment, maximum, cycle)),
            "increment": str(increment),
        }
    return result


def run_row(target, epoch, *, lock=False):
    row = target.execute(sql.SQL("SELECT state, progress FROM {} WHERE epoch = %s").format(RUNS) +
                         (sql.SQL(" FOR UPDATE") if lock else sql.SQL("")), (epoch,)).fetchone()
    if not row:
        raise CopyError("No existe el identificador de esta migración.")
    return row


def save_progress(target, epoch, progress):
    target.execute(sql.SQL("UPDATE {} SET progress = %s WHERE epoch = %s").format(RUNS),
                   (Jsonb(progress), epoch))


def check_bot_source(dataset):
    origin = urlsplit(os.environ.get("CHATTY_MIGRATION_SOURCE_STORAGE_URL", ""))
    match = re.fullmatch(r"([a-z0-9]{20})\.supabase\.co", origin.hostname or "")
    if not match or match[1] == CENTRAL_REF or (dataset in BOT_REFS and match[1] != BOT_REFS[dataset]):
        raise CopyError(f"El origen de Storage no corresponde a {dataset}.")
    check_project(os.environ.get("CHATTY_MIGRATION_SOURCE_DATABASE_URL", ""), match[1])


def prepare_staging(target):
    with target.transaction():
        target.execute("CREATE SCHEMA IF NOT EXISTS chatty_migration_stage")
        target.execute("REVOKE ALL ON SCHEMA chatty_migration_stage FROM PUBLIC, anon, authenticated, service_role")
        target.execute("""
            CREATE TABLE IF NOT EXISTS chatty_migration_stage.storage_copies (
                epoch text NOT NULL,
                dataset text NOT NULL,
                bucket text NOT NULL,
                object_key text NOT NULL,
                content_sha256 text NOT NULL,
                object_size bigint NOT NULL,
                mime_type text NOT NULL,
                PRIMARY KEY (epoch, dataset, bucket, object_key)
            )
        """)


def prepare(source, target, epoch, dataset, *, final):
    with source.transaction(), target.transaction():
        begin(source, source=True)
        begin(target)
        target.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(RUNS))
        if target.execute(sql.SQL(
            "SELECT EXISTS (SELECT 1 FROM {} WHERE epoch <> %s AND state IN ('preparing', 'frozen'))"
        ).format(RUNS), (epoch,)).fetchone()[0]:
            raise CopyError("Otra migración ya reservó este destino.")
        if not final:
            target.execute(sql.SQL(
                "INSERT INTO {} (epoch, state) VALUES (%s, 'preparing') ON CONFLICT DO NOTHING"
            ).format(RUNS), (epoch,))
        state, progress = run_row(target, epoch, lock=True)
        if state != ("frozen" if final else "preparing") or progress.get("rows_installed"):
            raise CopyError("La fase de copia no coincide con el estado del corte.")
        if final and (not progress.get("barrier_verified") or dataset not in progress.get("datasets", {})):
            raise CopyError(f"Falta el drenado verificado o la copia previa: {dataset}")
        origin = source.info
        fingerprint = hashlib.sha256(
            f"{origin.host}:{origin.port}:{origin.dbname}".encode()).hexdigest()
        previous = progress.get("datasets", {}).get(dataset)
        if previous and previous["source_fingerprint"] != fingerprint:
            raise CopyError(f"El origen no coincide con la copia anterior: {dataset}")
        copied = {table: stage_table(source, target, epoch, dataset, table)
                  for table in reviewed_tables(source, dataset)}
        sequence_values = stage_sequences(source, target, dataset)
        report = {"tables": copied, "sequences": sequence_values, "source_fingerprint": fingerprint,
                  "final": final, "captured_at": datetime.now(timezone.utc).isoformat()}
        if dataset in BOT_DATASETS:
            from crm_service.central_storage_copy import transfer_storage
            report["storage"] = transfer_storage(source, target, epoch, dataset)
        progress.setdefault("datasets", {})[dataset] = report
        save_progress(target, epoch, progress)
    return sum(item["rows"] for item in copied.values())


def prepare_fresh(target, epoch, dataset, *, final):
    """An explicitly authorized empty tenant, not a claimed source-data copy."""
    with target.transaction():
        begin(target)
        state, progress = run_row(target, epoch, lock=True)
        if state != ("frozen" if final else "preparing") or progress.get("rows_installed"):
            raise CopyError("El reinicio no coincide con la fase de migración.")
        if final and not progress.get("barrier_verified"):
            raise CopyError("El reinicio requiere el mismo drenado verificado.")
        tables = {}
        for table in BOT_TABLES:
            schema, name = qualified(dataset, table)
            fields = list(columns(target, schema, name))
            key = primary_key(target, schema, name)
            if not fields or not key:
                raise CopyError("Falta el esquema nuevo del negocio autorizado.")
            relation, stage = ident(schema, name), staging_name(epoch, dataset, table)
            if target.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM {})").format(relation)).fetchone()[0]:
                raise CopyError("El destino nuevo ya contiene datos; no se reiniciará otra vez.")
            target.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(stage))
            target.execute(sql.SQL("CREATE TABLE {} AS SELECT * FROM {} WITH NO DATA").format(stage, relation))
            tables[table] = {"columns": fields, "key": key, "rows": 0,
                             "sha256": stream_rows(target, stage, fields, key)}
        progress.setdefault("datasets", {})[dataset] = {
            "tables": tables, "sequences": {}, "strategy": "fresh",
            "final": final, "captured_at": datetime.now(timezone.utc).isoformat(),
            "storage": {"objects": 0, "bytes": 0, "strategy": "fresh"},
        }
        save_progress(target, epoch, progress)
    return 0


def install_table(target, epoch, dataset, table, snapshot, *, fresh_businesses=()):
    stage = staging_name(epoch, dataset, table)
    destination = ident(*qualified(dataset, table))
    fields, key = snapshot["columns"], snapshot["key"]
    if stream_rows(target, stage, fields, key) != snapshot["sha256"]:
        raise CopyError(f"La copia preparada cambió: {dataset}/{table}")
    selected = sql.SQL("(SELECT * FROM {} original WHERE TRUE").format(stage)
    if dataset in CRM_DATASETS and fresh_businesses:
        if "business_id" in fields and table not in CRM_CONFIG_TABLES | CRM_PILOT_TABLES:
            selected += sql.SQL(" AND (original.business_id IS NULL OR NOT (original.business_id = ANY({})))").format(sql.Literal(list(fresh_businesses)))
        elif table == "push_deliveries":
            selected += sql.SQL(""" AND EXISTS (SELECT 1 FROM chatty_crm.notification_events e
                WHERE e.id=original.event_id) AND EXISTS (SELECT 1 FROM chatty_crm.push_subscriptions p
                WHERE p.id=original.subscription_id)""")
    selected += sql.SQL(")")
    if dataset in CRM_DATASETS and table == "crm_agents":
        # Keep the commercial operator's preferences/queue identity. Union
        # memberships only; both untouched snapshots remain in private staging.
        target.execute(sql.SQL("""
            INSERT INTO {} AS d ({}) SELECT {} FROM {} s WHERE TRUE
            ON CONFLICT (profile) DO UPDATE SET allowed_businesses=(
                SELECT COALESCE(jsonb_agg(v ORDER BY v),'[]'::jsonb)::json FROM (
                    SELECT DISTINCT value v FROM jsonb_array_elements_text(
                        d.allowed_businesses::jsonb || excluded.allowed_businesses::jsonb)
                ) membership)
        """).format(destination, names(fields), names(fields), selected))
        missing = target.execute(sql.SQL("""
            SELECT EXISTS (SELECT 1 FROM {} s LEFT JOIN {} d USING (profile)
                WHERE d.profile IS NULL OR NOT (d.allowed_businesses::jsonb @> s.allowed_businesses::jsonb))
        """).format(selected, destination)).fetchone()[0]
        if missing:
            raise CopyError("No se conservaron todas las cuentas del CRM.")
        return
    target.execute(sql.SQL(
        "INSERT INTO {} ({}) OVERRIDING SYSTEM VALUE SELECT {} FROM {} WHERE TRUE ON CONFLICT ({}) DO NOTHING"
    ).format(destination, names(fields), names(fields), selected + sql.SQL(" s"), names(key)))
    extra = columns(target, *qualified(dataset, table)).keys() - set(fields)
    different = target.execute(sql.SQL("""
        SELECT EXISTS (SELECT 1 FROM {} s LEFT JOIN {} d USING ({})
            WHERE d.{} IS NULL OR to_jsonb(s) IS DISTINCT FROM (to_jsonb(d) - %s::text[]))
    """).format(selected, destination, names(key), ident(key[0])), (sorted(extra),)).fetchone()[0]
    if different:
        raise CopyError(f"Conflicto de filas existentes (no se reemplazaron): {dataset}/{table}")


def install(target, epoch):
    with target.transaction():
        begin(target)
        state, progress = run_row(target, epoch, lock=True)
        reports = progress.get("datasets", {})
        if (state != "frozen" or not progress.get("barrier_verified") or progress.get("rows_installed")
                or set(reports) != set(DATASETS) or not all(item["final"] for item in reports.values())):
            raise CopyError("Se requieren las cinco copias finales dentro del mismo corte congelado.")
        if target.execute("""
            SELECT EXISTS (SELECT 1 FROM public.chatty_runtime_businesses WHERE mode <> 'legacy')
                OR EXISTS (SELECT 1 FROM public.chatty_runtime_services WHERE mode <> 'legacy')
        """).fetchone()[0]:
            raise CopyError("El registro ya habilita escritores centrales.")
        relations = sorted({qualified(dataset, table)
                            for dataset, report in reports.items() for table in report["tables"]})
        for schema, table in relations:
            relation = ident(schema, table)
            target.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(relation))
            if target.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM {} LIMIT 1)").format(relation)).fetchone()[0]:
                raise CopyError(f"El destino contiene filas fuera de esta copia: {schema}/{table}")
        next_values = {}
        fresh_businesses = tuple(name for name, report in reports.items()
                                 if name in BOT_DATASETS and report.get("strategy") == "fresh")
        for dataset in DATASETS:
            report = reports[dataset]
            order = BOT_TABLES if dataset in BOT_DATASETS else CRM_TABLES
            for table in order:
                if table in report["tables"]:
                    install_table(target, epoch, dataset, table, report["tables"][table],
                                  fresh_businesses=fresh_businesses)
            schema = "public" if dataset in BOT_DATASETS else CRM_SCHEMA
            for name, sequence in report["sequences"].items():
                slot = (schema, name)
                next_values[slot] = max(next_values.get(slot, 0), int(sequence["next"]))
        for (schema, name), value in sorted(next_values.items()):
            increment, maximum, cycle = next(row[1:] for row in sequences(target, schema) if row[0] == name)
            current = sequence_next(target, schema, name, increment, maximum, cycle)
            target.execute(sql.SQL("ALTER SEQUENCE {} RESTART WITH {}").format(
                ident(schema, name), sql.Literal(max(current, value))))
        progress["rows_installed"] = True
        progress["fresh_businesses"] = list(fresh_businesses)
        progress["source_snapshots_retained"] = True
        save_progress(target, epoch, progress)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "final-copy", "install"))
    parser.add_argument("--epoch", required=True)
    parser.add_argument("--dataset", choices=DATASETS)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", args.epoch):
        parser.error("El identificador de la migración debe tener 16 a 80 caracteres.")
    if (args.action == "install") == bool(args.dataset):
        parser.error("Selecciona un origen para preparar o copiar, o todos mediante install.")
    try:
        with ExitStack() as stack:
            target = stack.enter_context(connect("CHATTY_CENTRAL_DATABASE_URL", central=True))
            if args.action == "install":
                install(target, args.epoch)
                print("Copias instaladas. Escritores centrales siguen deshabilitados.")
                return
            if args.dataset in BOT_DATASETS:
                check_bot_source(args.dataset)
            source = stack.enter_context(connect("CHATTY_MIGRATION_SOURCE_DATABASE_URL"))
            if args.action == "prepare":
                prepare_staging(target)
            rows = prepare(source, target, args.epoch, args.dataset, final=args.action == "final-copy")
            print(f"{args.dataset}: {rows} filas verificadas. No se cambió el destino de los procesos.")
    except CopyError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except (psycopg.Error, ValueError, OSError) as error:
        # PostgreSQL details can include the row or connection value that produced an error.
        print(f"La copia se detuvo: {type(error).__name__} (SQLSTATE {getattr(error, 'sqlstate', None) or 'desconocido'}).",
              file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
