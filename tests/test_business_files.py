import os
import unittest
from unittest.mock import Mock, patch

import main


class BusinessFilePromptTests(unittest.TestCase):
    def test_prompt_selects_only_the_referenced_saved_variable(self):
        variables = {
            "SYSTEM_PROMPT": "Use {{BUSINESS_FILE_CAFE_UNO}}",
            "BUSINESS_FILE_CAFE_UNO": "Cafe catalog",
            "BUSINESS_FILE_STORE_TWO": "Other catalog",
        }
        with patch.dict(os.environ, variables, clear=False):
            prompt = main.generate_system_prompt("inventory")

        self.assertEqual(prompt, "Use Cafe catalog")
        self.assertNotIn("Other catalog", prompt)

    def test_prompt_loads_a_referenced_url(self):
        response = Mock(content=b"Remote catalog")
        response.raise_for_status.return_value = None
        with patch.dict(
            os.environ,
            {
                "SYSTEM_PROMPT": "Use {{BUSINESS_FILE_CATALOG}}",
                "BUSINESS_FILE_CATALOG": "https://example.com/catalog.txt",
            },
            clear=False,
        ), patch.object(main.requests, "get", return_value=response) as get:
            prompt = main.generate_system_prompt("inventory")

        self.assertEqual(prompt, "Use Remote catalog")
        get.assert_called_once_with("https://example.com/catalog.txt", timeout=10)

    def test_inventory_placeholder_still_works(self):
        with patch.dict(
            os.environ, {"SYSTEM_PROMPT": "Inventory: {{inventory}}"}, clear=False
        ):
            self.assertEqual(
                main.generate_system_prompt("Coffee - $5"),
                "Inventory: Coffee - $5",
            )


if __name__ == "__main__":
    unittest.main()
