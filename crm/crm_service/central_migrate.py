"""Provision only the private CRM schema in Tanaka with the existing Alembic chain."""

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from crm_service.central_copy_connection import CopyError, connect
from crm_service.central_copy import CRM_REVISIONS, CRM_SCHEMA

BASE_REVISION = "0006_scoped_pilot"
INSIGHTS_REVISION = "0007_customer_insights"


def connection_for_migration():
    connection = connect("CHATTY_CENTRAL_DATABASE_URL", central=True)
    connection.autocommit = False
    return connection


def migrate(*, customer_insights=False, promotion_permissions=False):
    engine = create_engine("postgresql+psycopg://", hide_parameters=True,
                           creator=connection_for_migration, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS chatty_crm"))
            connection.execute(text("REVOKE ALL ON SCHEMA chatty_crm FROM PUBLIC, anon, authenticated, service_role"))
            connection.execute(text("SET LOCAL search_path = chatty_crm"))
            versioned = connection.execute(text(
                "SELECT to_regclass('chatty_crm.alembic_version') IS NOT NULL")).scalar_one()
            installed = None
            if versioned:
                revisions = connection.execute(text("SELECT version_num FROM chatty_crm.alembic_version")).scalars().all()
                if len(revisions) != 1 or revisions[0] not in CRM_REVISIONS:
                    raise CopyError("El esquema CRM central contiene una revisión no reconocida.")
                installed = revisions[0]
            elif connection.execute(text("""
                SELECT EXISTS (SELECT 1 FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'chatty_crm')
            """)).scalar_one():
                raise CopyError("El esquema CRM central contiene objetos sin historial de migración.")
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
            config.attributes.update({"central_connection": connection, "central_schema": CRM_SCHEMA})
            target = INSIGHTS_REVISION if customer_insights or installed == INSIGHTS_REVISION else BASE_REVISION
            command.upgrade(config, target)
            if promotion_permissions and target == BASE_REVISION:
                # The commercial API still accepts 0006. The pilot writer can
                # create only the additive evidence tables with its usual owner.
                revision = ScriptDirectory.from_config(config).get_revision(INSIGHTS_REVISION)
                with Operations.context(MigrationContext.configure(connection)):
                    revision.module.create_permission_tables()
            actual = connection.execute(text("SELECT version_num FROM chatty_crm.alembic_version")).scalar_one()
            if actual != target:
                raise CopyError("Alembic no confirmó la revisión esperada.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--customer-insights", action="store_true")
    parser.add_argument("--promotion-permissions", action="store_true")
    args = parser.parse_args()
    try:
        migrate(customer_insights=args.customer_insights, promotion_permissions=args.promotion_permissions)
    except CopyError as error:
        raise SystemExit(str(error)) from None
    except Exception as error:
        # SQLAlchemy connection exceptions may render private connection settings.
        raise SystemExit(f"No se pudo preparar el esquema CRM central ({type(error).__name__}).") from None
    print("Esquema privado del CRM preparado en Tanaka. Ningún proceso fue cambiado.")
