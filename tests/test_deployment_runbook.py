from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_generic_runbook_covers_hidden_cross_provider_steps():
    runbook = (ROOT / "DEPLOY_NEW_BUSINESS.md").read_text(encoding="utf-8")

    required = (
        "WA_TOKEN=BOOTSTRAP_NOT_READY",
        "REDIS_URL=${{Redis.REDIS_URL}}",
        "Integrations → Webhooks",
        "secreto/token",
        "CHATWOOT_ASSIGNMENT_MODE=automatic",
        "CHATWOOT_ASSIGNMENT_MODE=fixed",
        "GITHUB_SI_PATH=src/clients/<slug>/system_instruction.txt",
        "<SLUG>_DASHBOARD_BACKEND_URL",
        "catalogos/<slug>.<ext>",
        "FOLLOW_UP_TEST_DELAY_SECONDS=10",
    )
    for text in required:
        assert text in runbook


def test_memos_runbook_points_to_generic_runbook():
    memos = (ROOT / "SETUP_MEMOS.md").read_text(encoding="utf-8")
    assert "(DEPLOY_NEW_BUSINESS.md)" in memos
    assert "CHATWOOT_ASSIGNMENT_MODE=automatic" in memos
