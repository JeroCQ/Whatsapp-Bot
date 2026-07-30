import os
import tempfile
import unittest
from unittest.mock import patch

import main


class BusinessPromptTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
