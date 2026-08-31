import importlib.util
import os
from pathlib import Path
from unittest import TestCase, mock


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.py"
REQUIRED_ENV = {
    "BUSINESS_ID": "memos",
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_KEY": "legacy-key",
    "WA_VERIFY_TOKEN": "verify",
    "WA_TOKEN": "whatsapp",
    "WA_PHONE_NUMBER_ID": "phone-id",
    "GEMINI_API_KEY": "gemini",
}


def load_config():
    spec = importlib.util.spec_from_file_location("config_under_test", CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.config


class SupabaseKeyConfigTests(TestCase):
    def test_outage_recovery_delay_defaults_to_one(self):
        with mock.patch.dict(os.environ, REQUIRED_ENV, clear=True):
            self.assertEqual(load_config().GEMINI_OUTAGE_RECOVERY_DELAY_SECONDS, 1)

    def test_outage_recovery_delay_accepts_zero_and_rejects_invalid_values(self):
        with mock.patch.dict(os.environ, {**REQUIRED_ENV, "GEMINI_OUTAGE_RECOVERY_DELAY_SECONDS": "0"}, clear=True):
            self.assertEqual(load_config().GEMINI_OUTAGE_RECOVERY_DELAY_SECONDS, 0)
        for value in ("-1", "later"):
            with self.subTest(value=value), mock.patch.dict(
                os.environ, {**REQUIRED_ENV, "GEMINI_OUTAGE_RECOVERY_DELAY_SECONDS": value}, clear=True
            ):
                with self.assertRaisesRegex(ValueError, "non-negative number"):
                    load_config()

    def test_prefers_service_role_key(self):
        env = {**REQUIRED_ENV, "SUPABASE_SERVICE_ROLE_KEY": "service-role-key"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(load_config().SUPABASE_KEY, "service-role-key")

    def test_keeps_legacy_key_fallback(self):
        with mock.patch.dict(os.environ, REQUIRED_ENV, clear=True):
            self.assertEqual(load_config().SUPABASE_KEY, "legacy-key")

    def test_prefers_explicit_dashboard_github_values_over_railway_metadata(self):
        env = {
            **REQUIRED_ENV,
            "RAILWAY_GIT_REPO_OWNER": "railway-owner",
            "RAILWAY_GIT_REPO_NAME": "railway-repo",
            "RAILWAY_GIT_BRANCH": "feature-branch",
            "GITHUB_OWNER": "manual-owner",
            "GITHUB_REPO": "manual-repo",
            "GITHUB_BRANCH": "manual-branch",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = load_config()
            self.assertEqual(config.GITHUB_OWNER, "manual-owner")
            self.assertEqual(config.GITHUB_REPO, "manual-repo")
            self.assertEqual(config.GITHUB_BRANCH, "manual-branch")

    def test_keeps_manual_github_metadata_fallbacks(self):
        env = {
            **REQUIRED_ENV,
            "GITHUB_OWNER": "manual-owner",
            "GITHUB_REPO": "manual-repo",
            "GITHUB_BRANCH": "manual-branch",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = load_config()
            self.assertEqual(config.GITHUB_OWNER, "manual-owner")
            self.assertEqual(config.GITHUB_REPO, "manual-repo")
            self.assertEqual(config.GITHUB_BRANCH, "manual-branch")

    def test_normalizes_unsupported_gemini_latest_alias(self):
        env = {**REQUIRED_ENV, "GEMINI_DASHBOARD_MODEL": "gemini-1.5-flash-latest"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(load_config().GEMINI_DASHBOARD_MODEL, "gemini-3.6-flash")

    def test_dashboard_model_defaults_to_flash(self):
        with mock.patch.dict(os.environ, REQUIRED_ENV, clear=True):
            self.assertEqual(load_config().GEMINI_DASHBOARD_MODEL, "gemini-3.6-flash")

    def test_dashboard_models_include_unique_fallbacks(self):
        env = {
            **REQUIRED_ENV,
            "GEMINI_DASHBOARD_MODEL": "gemini-2.5-flash",
            "GEMINI_DASHBOARD_FALLBACK_MODELS": "gemini-3.5-flash,gemini-3.6-flash",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                load_config().GEMINI_DASHBOARD_MODELS,
                ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"],
            )


    def test_dashboard_format_timeout_default(self):
        with mock.patch.dict(os.environ, REQUIRED_ENV, clear=True):
            self.assertEqual(load_config().DASHBOARD_FORMAT_TIMEOUT_SECONDS, 90)

    def test_dashboard_storage_timeout_default_supports_large_catalogs(self):
        with mock.patch.dict(os.environ, REQUIRED_ENV, clear=True):
            self.assertEqual(load_config().DASHBOARD_STORAGE_TIMEOUT_SECONDS, 300)


class ChatwootAssignmentConfigTests(TestCase):
    CHATWOOT_ENV = {
        **REQUIRED_ENV,
        "CHATWOOT_BASE_URL": "https://chatwoot.example.com",
        "CHATWOOT_API_TOKEN": "secret",
        "CHATWOOT_ACCOUNT_ID": "2",
        "CHATWOOT_INBOX_ID": "2",
        "CHATWOOT_WEBHOOK_SECRET": "webhook-secret",
    }

    def test_automatic_mode_accepts_missing_assignee(self):
        with mock.patch.dict(os.environ, {**self.CHATWOOT_ENV, "CHATWOOT_ASSIGNMENT_MODE": "automatic"}, clear=True):
            self.assertIsNone(load_config().CHATWOOT_ASSIGNEE_ID)

    def test_automatic_mode_retains_rollback_assignee(self):
        env = {**self.CHATWOOT_ENV, "CHATWOOT_ASSIGNMENT_MODE": " Automatic ", "CHATWOOT_ASSIGNEE_ID": "4"}
        with mock.patch.dict(os.environ, env, clear=True):
            config = load_config()
            self.assertEqual(config.CHATWOOT_ASSIGNMENT_MODE, "automatic")
            self.assertEqual(config.CHATWOOT_ASSIGNEE_ID, "4")

    def test_fixed_mode_requires_assignee(self):
        with mock.patch.dict(os.environ, {**self.CHATWOOT_ENV, "CHATWOOT_ASSIGNMENT_MODE": "fixed"}, clear=True):
            with self.assertRaisesRegex(ValueError, "required.*fixed"):
                load_config()

    def test_fixed_mode_accepts_positive_assignee(self):
        env = {**self.CHATWOOT_ENV, "CHATWOOT_ASSIGNMENT_MODE": "fixed", "CHATWOOT_ASSIGNEE_ID": "4"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(load_config().CHATWOOT_ASSIGNEE_ID, "4")

    def test_rejects_invalid_assignee_in_every_mode(self):
        for assignee in ("0", "-1", "agent-four"):
            env = {**self.CHATWOOT_ENV, "CHATWOOT_ASSIGNMENT_MODE": "automatic", "CHATWOOT_ASSIGNEE_ID": assignee}
            with self.subTest(assignee=assignee), mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    load_config()

    def test_missing_mode_preserves_legacy_fixed_assignee(self):
        env = {**self.CHATWOOT_ENV, "CHATWOOT_ASSIGNEE_ID": "4"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(load_config().CHATWOOT_ASSIGNMENT_MODE, "fixed")

    def test_missing_mode_without_assignee_defaults_to_automatic(self):
        with mock.patch.dict(os.environ, self.CHATWOOT_ENV, clear=True):
            self.assertEqual(load_config().CHATWOOT_ASSIGNMENT_MODE, "automatic")

    def test_blank_mode_uses_compatibility_default(self):
        env = {**self.CHATWOOT_ENV, "CHATWOOT_ASSIGNMENT_MODE": "", "CHATWOOT_ASSIGNEE_ID": "4"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(load_config().CHATWOOT_ASSIGNMENT_MODE, "fixed")

    def test_rejects_invalid_assignment_mode(self):
        env = {**self.CHATWOOT_ENV, "CHATWOOT_ASSIGNMENT_MODE": "round-robin"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "automatic.*fixed"):
                load_config()
