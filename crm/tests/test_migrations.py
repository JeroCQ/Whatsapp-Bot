import json
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
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004_delivery_receipts"


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


def test_delivery_upgrade_projects_existing_receipts_without_losing_audit(database_url, monkeypatch):
    parsed = make_url(database_url)
    name = "crm_test_delivery_upgrade_" + uuid.uuid4().hex[:10]
    url = parsed.set(database=name).render_as_string(hide_password=False)
    admin = create_engine(parsed, isolation_level="AUTOCOMMIT")
    engine = create_engine(url)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        monkeypatch.setenv("CRM_DATABASE_URL", url)
        command.upgrade(config, "0003_bot_commands")
        with engine.begin() as connection:
            for phone in ("existing", "other-conversation"):
                connection.execute(
                    text(
                        "INSERT INTO source_conversations "
                        "(business_id,phone_number,name,customer,state,handoff,warnings,dirty) "
                        "VALUES ('synthetic',:phone,'SYNTHETIC existing','{}','{}','{}','[]',true)"
                    ),
                    {"phone": phone},
                )
            for identifier, phone, role, content, metadata in (
                (
                    1,
                    "existing",
                    "asesor",
                    "SYNTHETIC reply",
                    {
                        "source_event_id": "existing-command",
                        "provider_message_id": "wamid.existing",
                        "delivery_status": "accepted",
                    },
                ),
                (
                    2,
                    "existing",
                    "system",
                    "DELIVERY_STATUS: read",
                    {
                        "source": "crm_delivery",
                        "command_id": "existing-command",
                        "provider_message_id": "wamid.existing",
                        "delivery_status": "read",
                    },
                ),
                (
                    3,
                    "other-conversation",
                    "system",
                    "DELIVERY_STATUS: failed",
                    {
                        "source": "crm_delivery",
                        "command_id": "existing-command",
                        "provider_message_id": "wamid.existing",
                        "delivery_status": "failed",
                    },
                ),
            ):
                connection.execute(
                    text(
                        "INSERT INTO source_messages "
                        "(business_id,source_id,phone_number,role,content,created_at,metadata,event_key) "
                        "VALUES ('synthetic',:id,:phone,:role,:content,now(),CAST(:metadata AS JSON),:event)"
                    ),
                    {
                        "id": identifier,
                        "phone": phone,
                        "role": role,
                        "content": content,
                        "metadata": json.dumps(metadata),
                        "event": f"synthetic-existing-{identifier}",
                    },
                )
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            original = connection.execute(
                text(
                    "SELECT delivery_status,metadata,delivery_revision FROM source_messages WHERE source_id=1"
                )
            ).one()
            assert original.delivery_status == "read" and original.delivery_revision > 0
            assert original.metadata["delivery_status"] == "accepted"
            assert connection.scalar(text("SELECT count(*) FROM source_messages")) == 3
            assert connection.scalar(text("SELECT count(*) FROM delivery_receipts")) == 2
            assert (
                connection.scalar(
                    text("SELECT matched_source_id FROM delivery_receipts WHERE event_source_id=2")
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT matched_source_id FROM delivery_receipts WHERE event_source_id=3")
                )
                is None
            )
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
