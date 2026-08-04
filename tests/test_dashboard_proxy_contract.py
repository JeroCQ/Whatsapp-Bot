from pathlib import Path


PROXY = (Path(__file__).resolve().parents[1] / "supabase/functions/dashboard-api/index.ts").read_text()
CONFIG = (Path(__file__).resolve().parents[1] / "supabase/config.toml").read_text()


def test_proxy_uses_secret_passwords_without_hardcoding_them():
    assert "TANAKA_DASHBOARD_PASSWORD" in PROXY
    assert "MEMOS_DASHBOARD_PASSWORD" in PROXY
    assert "TANA2026" not in PROXY
    assert "MEMO2026" not in PROXY


def test_proxy_enforces_password_client_isolation():
    assert 'payload.client_name !== client' in PROXY
    assert 'form.get("client_name") !== client' in PROXY
    assert 'searchParams.get("client_name") !== client' in PROXY


def test_password_only_proxy_disables_platform_jwt_check():
    assert "verify_jwt = false" in CONFIG
