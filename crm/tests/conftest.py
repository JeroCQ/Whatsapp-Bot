import base64
import os
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command
from alembic.config import Config
from crm_service.db import database
from crm_service.models import Base
from crm_service.settings import Business, Settings


@pytest.fixture(scope="session")
def database_url():
    url = os.environ["CRM_TEST_DATABASE_URL"]
    parsed = make_url(url)
    if (
        not (parsed.database or "").startswith("crm_test")
        or parsed.host not in {"localhost", "127.0.0.1", "::1"}
        or parsed.query
        or parsed.drivername != "postgresql+psycopg"
    ):
        raise RuntimeError("Tests require a disposable database named crm_test*")
    old = os.environ.get("CRM_DATABASE_URL")
    os.environ["CRM_DATABASE_URL"] = url
    command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")
    yield url
    if old is None:
        os.environ.pop("CRM_DATABASE_URL", None)
    else:
        os.environ["CRM_DATABASE_URL"] = old


@pytest.fixture
def settings(database_url):
    return Settings(
        database_url=database_url,
        jwt_secret="test-jwt-" + "x" * 40,
        sync_secret="test-sync-" + "y" * 40,
        businesses={
            name: Business(
                name,
                f"https://{name}.supabase.co",
                "synthetic-source-key",
                f"https://chatwoot-{name}.example.com",
                "1",
            )
            for name in ("tanaka", "memos", "velvet")
        },
    )


@pytest.fixture
def sessions(settings):
    engine, factory = database(settings)
    with engine.begin() as conn:
        names = ", ".join('"' + t.name + '"' for t in Base.metadata.sorted_tables)
        conn.execute(text("TRUNCATE " + names + " CASCADE"))
    yield factory
    engine.dispose()


@pytest.fixture
def vapid_key():
    private = ec.generate_private_key(ec.SECP256R1()).private_numbers().private_value
    return base64.urlsafe_b64encode(private.to_bytes(32, "big")).decode().rstrip("=")


@pytest.fixture
def notifications_settings(settings, vapid_key):
    return replace(
        settings,
        notifications_enabled=True,
        vapid_private_key=vapid_key,
        vapid_subject="mailto:tests@example.invalid",
    )


@pytest.fixture
def migrated_engine(database_url):
    engine = create_engine(database_url)
    yield engine
    engine.dispose()
