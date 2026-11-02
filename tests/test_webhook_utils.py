import unittest

import pytest

from webhook_utils import (
    chatwoot_event_identity,
    is_restart_command,
    is_simple_greeting,
    resolve_whatsapp_sender,
    valid_whatsapp_phone,
    valid_whatsapp_sender,
    valid_whatsapp_user_id,
    whatsapp_destination_matches,
    whatsapp_recipient_fields,
)


def test_whatsapp_destination_is_isolated_from_other_meta_numbers():
    tanaka = {"metadata": {"phone_number_id": "tanaka-number"}}
    assert whatsapp_destination_matches(tanaka, "tanaka-number")
    assert not whatsapp_destination_matches(tanaka, "velvet-number")
    assert not whatsapp_destination_matches({}, "tanaka-number")


def test_whatsapp_sender_accepts_phone_identifiers():
    assert valid_whatsapp_sender("573025991292")
    assert not valid_whatsapp_sender(None)
    assert not valid_whatsapp_sender(573025991292)
    assert not valid_whatsapp_sender("not-a-phone")


@pytest.mark.parametrize("identity", ["CO.Example01", "US.aBc123", "CO.ENT." + "A" * 128])
def test_opaque_identity_is_preserved_in_meta_recipient_contract(identity):
    assert valid_whatsapp_sender(identity)
    assert valid_whatsapp_user_id(identity)
    assert not valid_whatsapp_phone(identity)
    assert whatsapp_recipient_fields(identity) == {"recipient_type": "individual", "recipient": identity}


@pytest.mark.parametrize("identity", [None, 15550000001, "@username", "co.Example", "CO.", "CO.a_b", "CO.ENT." + "A" * 129, "١٥٥٥٠٠٠٠٠٠١", "CO.Example\n"])
def test_invalid_identity_never_becomes_an_outbound_recipient(identity):
    assert not valid_whatsapp_sender(identity)
    with pytest.raises(ValueError):
        whatsapp_recipient_fields(identity)


def test_phone_keeps_existing_meta_destination_contract():
    assert whatsapp_recipient_fields("15550000001") == {"to": "15550000001"}


def test_batch_contact_selection_uses_exact_alias_instead_of_first_contact():
    contacts = [
        {"user_id": "CO.OtherUser", "wa_id": "15550000002", "profile": {"name": "Other customer"}},
        {"user_id": "CO.ThisUser", "wa_id": "15550000001", "profile": {"name": "Matched customer"}},
    ]
    assert resolve_whatsapp_sender({"from_user_id": "CO.ThisUser"}, contacts) == ("15550000001", "Matched customer")


def test_ambiguous_contact_alias_never_attaches_a_phone_history():
    contacts = [
        {"user_id": "CO.SharedAlias", "wa_id": "15550000001"},
        {"user_id": "CO.SharedAlias", "wa_id": "15550000002"},
    ]
    assert resolve_whatsapp_sender({"from_user_id": "CO.SharedAlias"}, contacts) == ("CO.SharedAlias", "Cliente")


def test_single_contact_fallback_is_explicit_and_not_available_to_batches():
    contact = {"wa_id": "15550000001", "profile": {"name": "Single customer"}}
    assert resolve_whatsapp_sender({}, [contact]) == (None, "Cliente")
    assert resolve_whatsapp_sender({}, [contact], allow_single_contact=True) == ("15550000001", "Single customer")
    assert resolve_whatsapp_sender({}, [contact, {"wa_id": "15550000002"}], allow_single_contact=True) == (None, "Cliente")


def test_phone_from_message_is_not_overwritten_by_conflicting_contact_phone():
    message = {"from": "15550000001", "from_user_id": "CO.SomeUser"}
    contact = {"wa_id": "15550000002", "user_id": "CO.SomeUser"}
    assert resolve_whatsapp_sender(message, [contact])[0] == "15550000001"


def test_username_is_only_a_display_label_never_a_routing_identity():
    contact = {"user_id": "CO.ProfileUser", "profile": {"username": "shop_customer"}}
    assert resolve_whatsapp_sender({"from_user_id": "CO.ProfileUser"}, [contact]) == ("CO.ProfileUser", "shop_customer")
    assert resolve_whatsapp_sender({}, [{"profile": {"username": "shop_customer"}}], allow_single_contact=True)[0] is None


def test_wamid_does_not_supply_an_identity():
    assert resolve_whatsapp_sender({"id": "wamid.some-opaque-event"}, [])[0] is None


def test_malformed_contact_metadata_does_not_break_message_identity():
    assert resolve_whatsapp_sender({"from_user_id": "CO.ExampleUser"}, {"profile": "invalid"}) == ("CO.ExampleUser", "Cliente")
    assert resolve_whatsapp_sender({"from_user_id": "CO.ExampleUser"}, [{"user_id": "CO.ExampleUser", "profile": []}]) == ("CO.ExampleUser", "Cliente")


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
