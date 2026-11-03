import os
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from crm_service.settings import Business, Settings
from crm_service.synthetic import (
    BUSINESSES,
    assert_notifications_disabled,
    disposable_url,
    fixture_rows,
    mark_empty_database,
    migrate_crm,
    seed_source,
    synchronize,
    verify_api,
    write_config,
)


@pytest.mark.parametrize(
    "url,source",
    [
        ("postgresql+psycopg://reader@production.example.com/crm_test_source_one", True),
        ("postgresql+psycopg://reader@127.0.0.1/production", False),
        ("postgresql+psycopg://reader@127.0.0.1/crm_test_source_one?host=production.example.com", True),
        ("postgresql+psycopg://reader@127.0.0.1/crm_test", True),
        ("postgresql://reader@127.0.0.1/crm_test", False),
        ("postgresql+psycopg://reader@db.supabase.co/crm_staging_source_one", True),
    ],
)
def test_synthetic_commands_reject_non_disposable_or_remote_destinations(url, source):
    with pytest.raises(ValueError, match="loopback disposable"):
        disposable_url(url, source=source)


def test_synthetic_commands_refuse_enabled_notifications(monkeypatch):
    monkeypatch.setenv("NOTIFICATIONS_ENABLED", "true")
    with pytest.raises(ValueError, match="NOTIFICATIONS_ENABLED=false"):
        assert_notifications_disabled()


def test_fixture_identity_is_shared_but_business_metadata_is_isolated():
    first, second = [fixture_rows(business) for business in BUSINESSES]
    assert {c["phone_number"] for c in first["customers"]} == {c["phone_number"] for c in second["customers"]}
    assert {m["metadata"]["business_id"] for m in first["message_logs"]} == {BUSINESSES[0]}
    assert {m["metadata"]["business_id"] for m in second["message_logs"]} == {BUSINESSES[1]}


def test_generated_api_config_has_no_source_credentials(tmp_path):
    source_urls = {BUSINESSES[0]: "first-synthetic-secret", BUSINESSES[1]: "second-synthetic-secret"}
    write_config(tmp_path, "postgresql+psycopg://crm@127.0.0.1/crm_test", source_urls)
    api = (tmp_path / "api.env").read_text()
    worker = (tmp_path / "worker.env").read_text()
    assert "READONLY_DATABASE_URL" not in api and "SERVICE_ROLE_KEY" not in api
    assert all(secret in worker and secret not in api for secret in source_urls.values())
    assert os.stat(tmp_path / "api.env").st_mode & 0o777 == 0o600
    assert os.stat(tmp_path / "worker.env").st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="overwrite"):
        write_config(tmp_path, "postgresql+psycopg://crm@127.0.0.1/crm_test", source_urls)


def test_verify_rejects_existing_crm_without_fixture_marker(settings, sessions):
    with pytest.raises(ValueError, match="marker is required"):
        verify_api(settings)


def test_failed_sync_is_not_hidden_by_an_older_complete_baseline(settings, sessions, monkeypatch):
    from crm_service.models import SyncStatus

    settings = replace(settings, businesses={name: Business(name, "", "") for name in BUSINESSES})
    with sessions.begin() as session:
        for name in BUSINESSES:
            session.add(SyncStatus(business_id=name, baseline_complete=True))
    monkeypatch.setattr("crm_service.synthetic.require_marked_crm", lambda _: None)
    monkeypatch.setattr("crm_service.synthetic.Synchronizer.run", lambda self, name: {name: "failed"})
    with pytest.raises(RuntimeError, match="failed or is in backoff"):
        synchronize(settings)


def test_two_real_synthetic_sources_to_real_crm_postgres(database_url, monkeypatch):
    monkeypatch.setenv("NOTIFICATIONS_ENABLED", "false")
    parsed = make_url(database_url)
    disposable_url(database_url)
    suffix = uuid.uuid4().hex[:12]
    names = ["crm_test_staging_" + suffix, "crm_test_source_one_" + suffix, "crm_test_source_two_" + suffix]
    urls = [parsed.set(database=name).render_as_string(hide_password=False) for name in names]
    admin = create_engine(parsed, isolation_level="AUTOCOMMIT", hide_parameters=True)
    readers = []
    try:
        with admin.connect() as connection:
            for name in names:
                connection.execute(text(f'CREATE DATABASE "{name}"'))
        migrate_crm(urls[0])
        proposal = Path(__file__).parents[1] / "security" / "isolated_source_proposal.sql"
        read_urls = {}
        for business, url in zip(BUSINESSES, urls[1:], strict=True):
            read_urls[business] = seed_source(url, business, proposal)
            readers.append(make_url(read_urls[business]).username)
        settings = Settings(
            database_url=urls[0],
            jwt_secret="synthetic-jwt-" + "x" * 40,
            sync_secret="synthetic-sync-" + "y" * 40,
            businesses={
                business: Business(business, "", "", readonly_database_url=read_urls[business])
                for business in BUSINESSES
            },
            notifications_enabled=False,
        )
        assert set(synchronize(settings).values()) == {"ok"}
        assert set(synchronize(settings).values()) == {"ok"}
        result = verify_api(settings)
        assert result == {
            "status": "verified",
            "businesses": list(BUSINESSES),
            "conversations": 8,
            "external_messages": 0,
            "notification_events": 0,
        }
        assert verify_api(settings)["conversations"] == 8
        with pytest.raises(ValueError, match="Notifications"):
            verify_api(replace(settings, notifications_enabled=True))
        # The loader never claims an ordinary existing database as a fixture DB.
        source_engine = create_engine(urls[1], hide_parameters=True)
        try:
            with source_engine.begin() as connection:
                connection.execute(text("CREATE VIEW public.unrelated_work AS SELECT 1 AS value"))
            with pytest.raises(ValueError, match="tables outside"):
                mark_empty_database(source_engine, source=True)
            with source_engine.begin() as connection:
                connection.execute(text("DROP VIEW public.unrelated_work"))
                connection.execute(text("DROP TABLE public.crm_synthetic_source_marker"))
            with pytest.raises(ValueError, match="existing database"):
                mark_empty_database(source_engine, source=True)
        finally:
            source_engine.dispose()
    finally:
        with admin.connect() as connection:
            for name in names:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            for reader in readers:
                assert reader and reader.startswith("crm_staging_reader_")
                connection.execute(text(f'DROP ROLE IF EXISTS "{reader}"'))
        admin.dispose()
