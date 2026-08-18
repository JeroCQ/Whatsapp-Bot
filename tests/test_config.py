import importlib.util
import os
from pathlib import Path
from unittest import TestCase, mock


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.py"
REQUIRED_ENV = {
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
    def test_prefers_service_role_key(self):
        env = {**REQUIRED_ENV, "SUPABASE_SERVICE_ROLE_KEY": "service-role-key"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(load_config().SUPABASE_KEY, "service-role-key")

    def test_keeps_legacy_key_fallback(self):
        with mock.patch.dict(os.environ, REQUIRED_ENV, clear=True):
            self.assertEqual(load_config().SUPABASE_KEY, "legacy-key")

    def test_prefers_railway_git_metadata_over_manual_github_values(self):
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
            self.assertEqual(config.GITHUB_OWNER, "railway-owner")
            self.assertEqual(config.GITHUB_REPO, "railway-repo")
            self.assertEqual(config.GITHUB_BRANCH, "feature-branch")

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

    def test_selects_memos_catalog(self):
        env = {**REQUIRED_ENV, "BUSINESS_CLIENT": "memos", "catalogo_memos": "[{}]"}
        with mock.patch.dict(os.environ, env, clear=True):
            config = load_config()
            self.assertEqual(config.BUSINESS_CLIENT, "memos")
            self.assertEqual(config.presaved_files_for_business(), ("[{}]", "catalogo_memos"))

    def test_rejects_unknown_business(self):
        env = {**REQUIRED_ENV, "BUSINESS_CLIENT": "unknown"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "BUSINESS_CLIENT"):
                load_config()
