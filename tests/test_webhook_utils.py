import unittest

from webhook_utils import (
    chatwoot_event_identity,
    is_restart_command,
    is_simple_greeting,
    valid_whatsapp_sender,
    whatsapp_destination_matches,
    whatsapp_sender,
)


def test_whatsapp_destination_is_isolated_from_other_meta_numbers():
    tanaka = {"metadata": {"phone_number_id": "tanaka-number"}}
    assert whatsapp_destination_matches(tanaka, "tanaka-number")
    assert not whatsapp_destination_matches(tanaka, "velvet-number")
    assert not whatsapp_destination_matches({}, "tanaka-number")


def test_whatsapp_sender_must_be_a_real_phone_identifier():
    assert valid_whatsapp_sender("573025991292")
    assert valid_whatsapp_sender("1733747451036137")
    assert not valid_whatsapp_sender(None)
    assert not valid_whatsapp_sender(573025991292)
    assert not valid_whatsapp_sender("not-a-phone")
    assert not valid_whatsapp_sender("1" * 26)


def test_whatsapp_sender_prefers_message_from():
    contacts = [{"wa_id": "573111111111"}]
    assert whatsapp_sender({"from": "573222222222"}, contacts) == "573222222222"


def test_whatsapp_sender_recovers_from_one_meta_contact():
    contacts = [{"wa_id": "573172807459", "profile": {"name": "Cliente"}}]
    assert whatsapp_sender({"type": "text"}, contacts) == "573172807459"


def test_whatsapp_sender_accepts_a_long_business_scoped_meta_identifier():
    contacts = [{"wa_id": "1733747451036137", "profile": {"name": "Manager"}}]
    assert whatsapp_sender({"from": "1733747451036137"}, contacts) == "1733747451036137"


def test_whatsapp_sender_does_not_guess_an_invalid_or_ambiguous_contact():
    assert whatsapp_sender({}, [{"wa_id": "invalid"}]) is None
    assert whatsapp_sender({}, [{"wa_id": "573111111111"}, {"wa_id": "573222222222"}]) is None


def test_whatsapp_sender_prefers_message_from():
    contacts = [{"wa_id": "573111111111"}]
    assert whatsapp_sender({"from": "573222222222"}, contacts) == "573222222222"


def test_whatsapp_sender_recovers_from_one_meta_contact():
    contacts = [{"wa_id": "573172807459", "profile": {"name": "Cliente"}}]
    assert whatsapp_sender({"type": "text"}, contacts) == "573172807459"


def test_whatsapp_sender_does_not_guess_an_invalid_or_ambiguous_contact():
    assert whatsapp_sender({}, [{"wa_id": "invalid"}]) is None
    assert whatsapp_sender({}, [{"wa_id": "573111111111"}, {"wa_id": "573222222222"}]) is None


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
