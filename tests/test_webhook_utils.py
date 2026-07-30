import unittest

from webhook_utils import chatwoot_event_identity, is_restart_command, is_simple_greeting


class WebhookUtilsTests(unittest.TestCase):
    def test_restart_accepts_both_commands(self):
        self.assertTrue(is_restart_command(" /restart "))
        self.assertTrue(is_restart_command("/RESET"))
        self.assertFalse(is_restart_command("restart"))

    def test_simple_greeting_is_recognized_without_matching_long_messages(self):
        self.assertTrue(is_simple_greeting("¡Hola!"))
        self.assertFalse(is_simple_greeting("Hola, necesito cotizar un envío"))

    def test_status_event_does_not_reuse_conversation_id_as_event_id(self):
        payload = {
            "event": "conversation_status_changed",
            "id": 54,
            "status": "resolved",
            "updated_at": "2026-07-30T21:10:00Z",
        }
        self.assertEqual(
            chatwoot_event_identity(payload),
            "conversation_status_changed:54:resolved:2026-07-30T21:10:00Z",
        )

    def test_status_without_timestamp_is_not_deduplicated_forever(self):
        payload = {"event": "conversation_status_changed", "id": 54, "status": "resolved"}
        self.assertEqual(chatwoot_event_identity(payload), "")

    def test_message_event_uses_message_specific_identity(self):
        self.assertEqual(
            chatwoot_event_identity({"event": "message_created", "id": 768513753}),
            "message_created:768513753",
        )


if __name__ == "__main__":
    unittest.main()
