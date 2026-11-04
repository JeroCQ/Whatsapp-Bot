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
    monkeypatch.setenv("FUTURE_SHOP_SOURCE_SUPABASE_SERVICE_ROLE_KEY", "administrative-key-must-never-load")
    settings = Settings.from_env()
    assert set(settings.businesses) == {"future_shop"}
    assert settings.businesses["future_shop"].source_key == ""
    assert "administrative-key-must-never-load" not in repr(settings)


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


def test_media_inherits_all_configured_businesses_and_never_unknown_businesses(monkeypatch):
    basic_env(monkeypatch)
    businesses = ("tanaka", "memos", "velvet", "briosos", "future_shop")
    monkeypatch.setenv("CRM_BUSINESSES", ",".join(businesses))
    monkeypatch.setenv("CRM_MEDIA_ENABLED", "true")
    monkeypatch.delenv("CRM_MEDIA_ALLOWED_BUSINESSES", raising=False)
    for business in businesses:
        prefix = business.upper()
        monkeypatch.setenv(prefix + "_HUMAN_CONTROL_ENABLED", "true")
        monkeypatch.setenv(prefix + "_BOT_API_URL", f"https://synthetic-{business}.invalid")
        monkeypatch.setenv(prefix + "_BOT_CONTROL_SECRET", "synthetic-control-" + "x" * 40)
    settings = Settings.from_env()
    assert settings.media_businesses is None
    assert all(settings.media_allowed(business) for business in businesses)
    assert not settings.media_allowed("not_registered")
    monkeypatch.setenv("CRM_MEDIA_ENABLED", "false")
    assert not any(Settings.from_env().media_allowed(business) for business in businesses)


def test_explicit_media_scope_can_restrict_or_disable_businesses(monkeypatch):
    basic_env(monkeypatch)
    monkeypatch.setenv("CRM_BUSINESSES", "tanaka,memos")
    monkeypatch.setenv("CRM_MEDIA_ENABLED", "true")
    for business in ("tanaka", "memos"):
        prefix = business.upper()
        monkeypatch.setenv(prefix + "_HUMAN_CONTROL_ENABLED", "true")
        monkeypatch.setenv(prefix + "_BOT_API_URL", f"https://synthetic-{business}.invalid")
        monkeypatch.setenv(prefix + "_BOT_CONTROL_SECRET", "synthetic-control-" + "x" * 40)
    monkeypatch.setenv("CRM_MEDIA_ALLOWED_BUSINESSES", "tanaka")
    settings = Settings.from_env()
    assert settings.media_allowed("tanaka") and not settings.media_allowed("memos")
    monkeypatch.setenv("CRM_MEDIA_ALLOWED_BUSINESSES", "")
    assert not any(Settings.from_env().media_allowed(business) for business in ("tanaka", "memos"))
