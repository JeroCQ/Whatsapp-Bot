import uuid
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from alembic import command
from alembic.config import Config
from crm_service.models import Base


def test_migrations_roundtrip_and_metadata_parity(database_url, migrated_engine):
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.downgrade(config, "base")
    assert not (set(Base.metadata.tables) & set(inspect(migrated_engine).get_table_names()))
    command.upgrade(config, "head")
    command.check(config)
    assert set(Base.metadata.tables) <= set(inspect(migrated_engine).get_table_names())
    with migrated_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002_sync_controls"


def test_upgrade_preserves_existing_crm_data(database_url, monkeypatch):
    parsed = make_url(database_url)
    name = "crm_test_upgrade_" + uuid.uuid4().hex[:12]
    url = parsed.set(database=name).render_as_string(hide_password=False)
    admin = create_engine(parsed, isolation_level="AUTOCOMMIT")
    engine = create_engine(url)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        monkeypatch.setenv("CRM_DATABASE_URL", url)
        command.upgrade(config, "0001_crm")
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO crm_agents VALUES ('employee-existing', '[\"synthetic\"]', '{}')")
            )
            connection.execute(
                text(
                    "INSERT INTO source_conversations VALUES ('synthetic', 'same-contact', "
                    "'SYNTHETIC existing', '{}', '{}', '{}', '[]')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO crm_conversations VALUES ('synthetic', 'same-contact', "
                    "'awaiting_human', 'qualified', 'employee-existing', '[\"important\"]', '{}', 'original-sort', 2, 7)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO crm_notes VALUES ('note-existing', 'synthetic', 'same-contact', "
                    "'employee-existing', 'SYNTHETIC preserved note', now())"
                )
            )
            connection.execute(
                text("INSERT INTO sync_status VALUES ('synthetic', true, now(), now(), NULL, '[]')")
            )
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            crm = connection.execute(
                text("SELECT stage,assigned_to,tags,version FROM crm_conversations")
            ).one()
            assert tuple(crm) == ("qualified", "employee-existing", ["important"], 7)
            assert connection.scalar(text("SELECT body FROM crm_notes")) == "SYNTHETIC preserved note"
            assert connection.scalar(text("SELECT dirty FROM source_conversations")) is True
            status = connection.execute(
                text("SELECT baseline_complete,failures,metrics FROM sync_status")
            ).one()
            assert tuple(status) == (True, 0, {})
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
