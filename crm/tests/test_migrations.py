from pathlib import Path

from sqlalchemy import inspect, text

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
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001_crm"
