import unittest

from chatwoot_events import conversation_id, event_id


class ChatwootEventTests(unittest.TestCase):
    def test_status_event_does_not_reuse_conversation_id_as_event_id(self):
        opened = {"event": "conversation_status_changed", "id": 53, "status": "open", "updated_at": 10}
        resolved = {"event": "conversation_status_changed", "id": 53, "status": "resolved", "updated_at": 20}
        self.assertNotEqual(event_id(opened), event_id(resolved))
        self.assertNotEqual(event_id(resolved), "53")

    def test_duplicate_status_payload_has_stable_id(self):
        payload = {"event": "conversation_status_changed", "id": 53, "status": "resolved"}
        reordered = {"status": "resolved", "id": 53, "event": "conversation_status_changed"}
        self.assertEqual(event_id(payload), event_id(reordered))

    def test_message_ids_are_scoped_by_event_type(self):
        self.assertEqual(event_id({"event": "message_created", "id": 766840897}), "message_created:766840897")

    def test_conversation_id_supports_both_chatwoot_shapes(self):
        self.assertEqual(conversation_id({"conversation": {"id": 12}, "id": 99}), 12)
        self.assertEqual(conversation_id({"id": 53}), 53)


if __name__ == "__main__":
    unittest.main()
