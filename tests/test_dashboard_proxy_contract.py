from pathlib import Path


PROXY = (Path(__file__).resolve().parents[1] / "supabase/functions/dashboard-api/index.ts").read_text()
CONFIG = (Path(__file__).resolve().parents[1] / "supabase/config.toml").read_text()


def test_proxy_uses_secret_passwords_without_hardcoding_them():
    assert '`PASSWORD_${prefix}`' in PROXY
    assert '`${prefix}_DASHBOARD_PASSWORD`' in PROXY
    assert "TANA2026" not in PROXY
    assert "MEMO2026" not in PROXY


def test_proxy_enforces_password_client_isolation():
    assert 'incomingUrl.searchParams.set("client_name", client)' in PROXY
    assert "delete payload.client_name" in PROXY
    assert 'form.delete("client_name")' in PROXY
    assert '"X-Client-Name": client' in PROXY


def test_proxy_supports_multi_catalog_contract_and_isolated_backends():
    assert '`${prefix}_DASHBOARD_BACKEND_URL`' in PROXY
    assert '`${prefix}_DASHBOARD_API_KEY`' in PROXY
    assert 'route === "catalogs"' in PROXY
    assert "catalogs\\/catalogo_" in PROXY
    assert '"catalog-prompt-preview"' in PROXY
    assert 'route === "manual-handoff"' in PROXY
    assert 'return jsonResponse(424' in PROXY


def test_password_only_proxy_disables_platform_jwt_check():
    assert "verify_jwt = false" in CONFIG
