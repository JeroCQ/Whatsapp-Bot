import unittest
import os
from unittest.mock import Mock, patch

for key in ("BUSINESS_ID", "SUPABASE_URL", "SUPABASE_KEY", "WA_VERIFY_TOKEN", "WA_TOKEN", "WA_PHONE_NUMBER_ID", "GEMINI_API_KEY"):
    os.environ.setdefault(key, "test-value")

import chatwoot_api


class ChatwootDiagnosticTests(unittest.TestCase):
    @patch("chatwoot_api.get")
    def test_agent_bot_token_failure_has_actionable_message(self, mock_get):
        response = Mock(status_code=403, ok=False)
        mock_get.return_value = response
        with patch.multiple(
            chatwoot_api.config,
            CHATWOOT_BASE_URL="https://chatwoot.example.com",
            CHATWOOT_API_TOKEN="secret",
            CHATWOOT_ACCOUNT_ID="1",
            CHATWOOT_INBOX_ID="2",
        ):
            result = chatwoot_api.diagnose_connection()

        self.assertFalse(result.ok)
        self.assertEqual(result.status_code, 403)
        self.assertIn("Agent Bot", result.reason)
        self.assertNotIn("secret", result.reason)

    @patch("chatwoot_api.get")
    def test_success_checks_configured_inbox(self, mock_get):
        mock_get.return_value = Mock(status_code=200, ok=True)
        with patch.multiple(
            chatwoot_api.config,
            CHATWOOT_BASE_URL="https://chatwoot.example.com/",
            CHATWOOT_API_TOKEN="secret",
            CHATWOOT_ACCOUNT_ID="1",
            CHATWOOT_INBOX_ID="2",
        ):
            result = chatwoot_api.diagnose_connection()

        self.assertTrue(result.ok)
        self.assertEqual(mock_get.call_args.args[0], "https://chatwoot.example.com/api/v1/accounts/1/inboxes/2")

    def test_missing_configuration_does_not_make_request(self):
        with patch.multiple(
            chatwoot_api.config,
            CHATWOOT_BASE_URL=None,
            CHATWOOT_API_TOKEN=None,
            CHATWOOT_ACCOUNT_ID="1",
            CHATWOOT_INBOX_ID="2",
        ):
            result = chatwoot_api.diagnose_connection()

        self.assertFalse(result.ok)
        self.assertIn("CHATWOOT_BASE_URL", result.reason)


if __name__ == "__main__":
    unittest.main()
