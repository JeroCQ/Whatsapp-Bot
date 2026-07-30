import os
import tempfile
import unittest
from unittest.mock import patch

import main


class BusinessPromptTests(unittest.TestCase):
    def test_default_model_uses_supported_flash_alias(self):
        self.assertEqual(main.DEFAULT_GEMINI_MODEL, "gemini-flash-latest")

    def test_prompt_injects_only_referenced_business_file(self):
        with patch.object(main, "SYSTEM_PROMPT", "Catalog:\n{{file:catalog}}"), patch.dict(
            os.environ,
            {
                "BUSINESS_FILE_CATALOG": "text:Coffee - $4",
                "BUSINESS_FILE_PRIVATE": "text:must not be included",
            },
            clear=False,
        ):
            prompt = main.generate_system_prompt("ignored inventory")

        self.assertEqual(prompt, "Catalog:\nCoffee - $4")
        self.assertNotIn("must not be included", prompt)

    def test_prompt_supports_a_local_utf8_file(self):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as file:
            file.write("Horario: 9 a 5")
            path = file.name

        try:
            with patch.object(main, "SYSTEM_PROMPT", "{{file:hours}}"), patch.dict(
                os.environ, {"BUSINESS_FILE_HOURS": path}, clear=False
            ):
                self.assertEqual(main.generate_system_prompt(""), "Horario: 9 a 5")
        finally:
            os.unlink(path)

    def test_missing_file_is_marked_unavailable(self):
        with patch.object(main, "SYSTEM_PROMPT", "Use {{file:missing}}"), patch.dict(
            os.environ, {}, clear=True
        ):
            prompt = main.generate_system_prompt("")

        self.assertEqual(prompt, "Use [Business file missing is unavailable]")

    def test_inventory_placeholder_remains_supported(self):
        with patch.object(main, "SYSTEM_PROMPT", "Inventory:\n{{inventory}}"):
            self.assertEqual(main.generate_system_prompt("Tea - $2"), "Inventory:\nTea - $2")


class WebhookNormalizationTests(unittest.TestCase):
    def test_evolution_text_message(self):
        message = main.normalize_webhook_payload(
            {
                "event": "messages.upsert",
                "data": {
                    "key": {"remoteJid": "15551234567@s.whatsapp.net", "fromMe": False},
                    "message": {"conversation": "Hello"},
                },
            }
        )

        self.assertEqual(message.sender_id, "15551234567")
        self.assertEqual(message.message_type, "text")
        self.assertEqual(message.text_content, "Hello")

    def test_evolution_outgoing_message_is_ignored(self):
        message = main.normalize_webhook_payload(
            {
                "event": "messages.upsert",
                "data": {
                    "key": {"remoteJid": "15551234567@s.whatsapp.net", "fromMe": True},
                    "message": {"conversation": "Bot reply"},
                },
            }
        )

        self.assertIsNone(message)

    def test_chatwoot_incoming_message(self):
        message = main.normalize_webhook_payload(
            {
                "event": "message_created",
                "message_type": "incoming",
                "content": "Do you have coffee?",
                "conversation": {
                    "meta": {"sender": {"phone_number": "+15557654321"}}
                },
            }
        )

        self.assertEqual(message.sender_id, "15557654321")
        self.assertEqual(message.text_content, "Do you have coffee?")

    def test_original_payload_is_still_supported(self):
        message = main.normalize_webhook_payload(
            {"sender_id": "123", "message_type": "text", "text_content": "Hi"}
        )

        self.assertEqual(message.text_content, "Hi")

    def test_wrapped_evolution_payload_without_event(self):
        message = main.normalize_webhook_payload(
            {
                "body": {
                    "data": {
                        "sender": "15559876543@s.whatsapp.net",
                        "message": {"extendedTextMessage": {"text": "Wrapped hello"}},
                    }
                }
            }
        )

        self.assertEqual(message.sender_id, "15559876543")
        self.assertEqual(message.text_content, "Wrapped hello")

    def test_flat_legacy_evolution_payload(self):
        message = main.normalize_webhook_payload(
            {
                "sender": "15551112222@s.whatsapp.net",
                "message": {"conversation": "Legacy hello"},
            }
        )

        self.assertEqual(message.sender_id, "15551112222")
        self.assertEqual(message.text_content, "Legacy hello")

    def test_meta_cloud_api_payload(self):
        message = main.normalize_webhook_payload(
            {
                "object": "whatsapp_business_account",
                "entry": [
                    {
                        "changes": [
                            {
                                "value": {
                                    "messages": [
                                        {
                                            "from": "15553334444",
                                            "type": "text",
                                            "text": {"body": "Meta hello"},
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ],
            }
        )

        self.assertEqual(message.sender_id, "15553334444")
        self.assertEqual(message.text_content, "Meta hello")

    def test_database_is_optional_for_basic_messages(self):
        with patch.object(main, "database_url", None):
            self.assertEqual(
                main.get_client_state("123"),
                {"is_vip": False, "bot_paused": False},
            )
            self.assertEqual(
                main.get_active_inventory_string(),
                "No database inventory configured.",
            )


if __name__ == "__main__":
    unittest.main()
