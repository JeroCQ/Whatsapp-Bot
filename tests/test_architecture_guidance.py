from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_codex_guidance_requires_repeatable_fresh_and_existing_deployments():
    guidance = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "fresh isolated deployment" in guidance
    assert "existing deployment upgraded without losing its data" in guidance
    assert "Keep runtime code generic" in guidance
    assert "BUSINESS_ID" in guidance
    assert "update the canonical `supabase/bootstrap.sql`" in guidance
    assert "canonical `supabase/upgrade_existing_brand.sql`" in guidance
    assert "future arbitrary" in guidance


def test_existing_brand_runbook_preserves_brand_isolation_and_uses_generic_upgrade():
    runbook = (ROOT / "docs" / "UPGRADE_EXISTING_BRANDS.md").read_text(encoding="utf-8")

    assert "supabase/upgrade_existing_brand.sql" in runbook
    assert "No ejecutes `supabase/bootstrap.sql`" in runbook
    assert "BUSINESS_ID" in runbook
    assert "whatsapp-events-tanaka" in runbook
    assert "whatsapp-events-memos" in runbook
    assert "message_logs_role_check" in runbook
    assert "No copies la imagen Velvet" in runbook
    assert "CHATWOOT_API_INBOX_WEBHOOK_SECRET" in runbook
    assert "CHATWOOT_ASSIGNMENT_MODE=automatic" in runbook
    assert "nunca `hmac_token`" in runbook
    assert "API inbox a `delivered`" in runbook
