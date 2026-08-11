import asyncio
import os
import unittest
import time
from unittest.mock import AsyncMock, Mock, patch

import jwt

for key, value in {
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_KEY": "test-key",
    "WA_VERIFY_TOKEN": "verify",
    "WA_TOKEN": "wa-token",
    "WA_PHONE_NUMBER_ID": "phone-id",
    "GEMINI_API_KEY": "gemini-key",
}.items():
    os.environ.setdefault(key, value)

from kommo_api import (
    InvalidKommoPayload,
    InvalidKommoToken,
    continue_salesbot,
    extract_kommo_message,
    extract_widget_request,
    send_message_kommo,
    validate_return_url,
)


class KommoPayloadTests(unittest.TestCase):
    def test_extracts_direct_salesbot_payload(self):
        self.assertEqual(
            extract_kommo_message({"chat_id": "chat-1", "contact_id": 42, "message": " Hola "}),
            ("chat-1", "42", "Hola"),
        )

    def test_extracts_nested_payload(self):
        payload = {"payload": {"chat": {"id": "c1"}, "contact": {"id": 7}, "message": {"text": "Hola"}}}
        self.assertEqual(extract_kommo_message(payload), ("c1", "7", "Hola"))

    def test_accepts_kommo_talk_id_name(self):
        payload = {"talk_id": 12345, "contact_id": 7, "text": "Hola"}
        self.assertEqual(extract_kommo_message(payload), ("12345", "7", "Hola"))

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
            result = asyncio.run(send_message_kommo("12345", "Respuesta"))

        self.assertTrue(result)
        client.post.assert_awaited_once_with(
            "https://account.kommo.com/api/v4/talks/12345/send_message",
            headers={
                "Authorization": "Bearer private-token",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={"text": "Respuesta"},
        )


class KommoWidgetTests(unittest.TestCase):
    def _token(self):
        now = int(time.time())
        return jwt.encode(
            {
                "iss": "https://account.kommo.com",
                "client_uuid": "integration-id",
                "iat": now,
                "exp": now + 300,
            },
            "integration-secret",
            algorithm="HS256",
        )

    def test_extracts_authenticated_widget_request(self):
        payload = {
            "token": self._token(),
            "data": {"message": " Hola ", "contact_id": 42, "lead_id": 99},
            "return_url": "https://account.kommo.com/api/v4/salesbot/12/continue/34",
        }
        with (
            patch("kommo_api.config.KOMMO_BASE_URL", "https://account.kommo.com"),
            patch("kommo_api.config.KOMMO_INTEGRATION_SECRET", "integration-secret"),
            patch("kommo_api.config.KOMMO_INTEGRATION_ID", "integration-id"),
        ):
            result = extract_widget_request(payload)
        self.assertEqual(result.contact_id, "42")
        self.assertEqual(result.message_text, "Hola")

    def test_rejects_foreign_return_url(self):
        with patch("kommo_api.config.KOMMO_BASE_URL", "https://account.kommo.com"):
            with self.assertRaises(InvalidKommoPayload):
                validate_return_url("https://evil.example/api/v4/salesbot/12/continue/34")

    def test_rejects_invalid_widget_token(self):
        with (
            patch("kommo_api.config.KOMMO_BASE_URL", "https://account.kommo.com"),
            patch("kommo_api.config.KOMMO_INTEGRATION_SECRET", "integration-secret"),
        ):
            with self.assertRaises(InvalidKommoToken):
                extract_widget_request({"token": "invalid", "data": {}})

    def test_continues_salesbot_with_bearer_token_and_bounded_handlers(self):
        response = Mock()
        response.raise_for_status.return_value = None
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = response
        client.__aexit__.return_value = None
        return_url = "https://account.kommo.com/api/v4/salesbot/12/continue/34"
        with (
            patch("kommo_api.config.KOMMO_BASE_URL", "https://account.kommo.com"),
            patch("kommo_api.config.KOMMO_PRIVATE_TOKEN", "private-token"),
            patch("kommo_api.httpx.AsyncClient", return_value=client),
        ):
            result = asyncio.run(continue_salesbot(return_url, "Respuesta " * 30))
        self.assertTrue(result)
        request = client.post.await_args
        self.assertEqual(request.args[0], return_url)
        self.assertEqual(request.kwargs["headers"]["Authorization"], "Bearer private-token")
        handlers = request.kwargs["json"]["execute_handlers"]
        self.assertLessEqual(len(handlers), 10)
        self.assertTrue(all(len(item["params"]["value"]) <= 80 for item in handlers))


if __name__ == "__main__":
    unittest.main()
