import pytest

from crm_service.settings import Settings


def basic_env(monkeypatch):
    monkeypatch.setenv("CRM_DATABASE_URL", "postgresql+psycopg://crm@localhost/crm")
    monkeypatch.setenv("CRM_JWT_SECRET", "jwt-" + "x" * 40)
    monkeypatch.setenv("CRM_SYNC_SECRET", "sync-" + "y" * 40)


def test_future_business_requires_no_code_branch(monkeypatch):
    basic_env(monkeypatch)
    monkeypatch.setenv("CRM_BUSINESSES", "future_shop")
    monkeypatch.setenv("FUTURE_SHOP_SOURCE_SUPABASE_URL", "https://future.supabase.co")
    monkeypatch.setenv("FUTURE_SHOP_SOURCE_SUPABASE_SERVICE_ROLE_KEY", "synthetic")
    settings = Settings.from_env()
    assert set(settings.businesses) == {"future_shop"}
    assert settings.businesses["future_shop"].source_key == "synthetic"
    assert "synthetic" not in repr(settings)


def test_sources_must_be_isolated(monkeypatch):
    basic_env(monkeypatch)
    monkeypatch.setenv("CRM_BUSINESSES", "one,two")
    for prefix in ("ONE", "TWO"):
        monkeypatch.setenv(prefix + "_SOURCE_SUPABASE_URL", "https://same.supabase.co")
    with pytest.raises(ValueError, match="distinct isolated"):
        Settings.from_env()


@pytest.mark.parametrize("businesses", ["one,one", "../escape", "UPPER", "", "one-two"])
def test_invalid_business_registry(monkeypatch, businesses):
    basic_env(monkeypatch)
    monkeypatch.setenv("CRM_BUSINESSES", businesses)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_secret_domains_and_base_urls_are_not_arbitrary_paths(monkeypatch):
    basic_env(monkeypatch)
    monkeypatch.setenv("CRM_BUSINESSES", "one")
    monkeypatch.setenv("ONE_SOURCE_SUPABASE_URL", "https://name:secret@example.com/rest/v1")
    with pytest.raises(ValueError):
        Settings.from_env()
