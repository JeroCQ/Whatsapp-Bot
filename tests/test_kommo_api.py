import asyncio
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

for key, value in {
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_KEY": "test-key",
    "WA_VERIFY_TOKEN": "verify",
    "WA_TOKEN": "wa-token",
    "WA_PHONE_NUMBER_ID": "phone-id",
    "GEMINI_API_KEY": "gemini-key",
}.items():
    os.environ.setdefault(key, value)

from kommo_api import InvalidKommoPayload, extract_kommo_message, send_message_kommo


class KommoPayloadTests(unittest.TestCase):
    def test_extracts_direct_salesbot_payload(self):
        self.assertEqual(
            extract_kommo_message({"chat_id": "chat-1", "contact_id": 42, "message": " Hola "}),
            ("chat-1", "42", "Hola"),
        )

    def test_extracts_nested_payload(self):
        payload = {"payload": {"chat": {"id": "c1"}, "contact": {"id": 7}, "message": {"text": "Hola"}}}
        self.assertEqual(extract_kommo_message(payload), ("c1", "7", "Hola"))

    def test_rejects_incomplete_payload(self):
        with self.assertRaises(InvalidKommoPayload):
            extract_kommo_message({"chat_id": "c1"})


class KommoSendingTests(unittest.TestCase):
    def test_sends_bearer_token_and_expected_message_json(self):
        response = Mock()
        response.raise_for_status.return_value = None
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = response
        client.__aexit__.return_value = None
        with (
            patch("kommo_api.config.KOMMO_BASE_URL", "https://account.kommo.com"),
            patch("kommo_api.config.KOMMO_PRIVATE_TOKEN", "private-token"),
            patch("kommo_api.httpx.AsyncClient", return_value=client),
        ):
            result = asyncio.run(send_message_kommo("chat/1", "Respuesta"))

        self.assertTrue(result)
        client.post.assert_awaited_once_with(
            "https://account.kommo.com/api/v4/chats/chat%2F1/messages",
            headers={
                "Authorization": "Bearer private-token",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={"message": {"text": "Respuesta"}},
        )


if __name__ == "__main__":
    unittest.main()
