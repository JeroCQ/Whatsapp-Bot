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
