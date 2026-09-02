from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_existing_tanaka_upgrade_covers_current_runtime_schema():
    sql = (ROOT / "supabase" / "upgrade_existing_tanaka.sql").read_text(encoding="utf-8").lower()

    assert "add column if not exists follow_up_token" in sql
    assert "add column if not exists customer_data" in sql
    assert "add column if not exists order_summary" in sql
    assert "uq_conversation_states_active_chatwoot_conversation_id" in sql
    assert "idx_message_logs_phone_created_at" in sql
    assert "values ('catalogos', 'catalogos', true)" in sql


def test_existing_tanaka_upgrade_is_non_destructive():
    sql = (ROOT / "supabase" / "upgrade_existing_tanaka.sql").read_text(encoding="utf-8").lower()

    assert "drop table" not in sql
    assert "truncate " not in sql
    assert "delete from" not in sql


def test_new_project_bootstrap_includes_all_incremental_tanaka_features():
    sql = (ROOT / "supabase" / "bootstrap.sql").read_text(encoding="utf-8").lower()

    for required in (
        "processed_webhook_events",
        "add column if not exists chatwoot_conversation_id",
        "add column if not exists follow_up_token",
        "add column if not exists customer_data",
        "add column if not exists order_summary",
        "uq_conversation_states_active_chatwoot_conversation_id",
        "idx_message_logs_phone_created_at",
        "values ('catalogos', 'catalogos', true)",
        "create table if not exists public.dashboard_admins",
        "enable row level security",
        "revoke all on table public.dashboard_admins from anon, authenticated",
    ):
        assert required in sql

    assert "do not run upgrade_existing_tanaka.sql afterward" in sql
