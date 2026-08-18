"""Regression checks for selecting one isolated business per deployment."""

from pathlib import Path
import unittest


class BusinessCatalogSelectionTests(unittest.TestCase):
    def test_both_catalog_variables_remain_declared(self):
        source = Path("config.py").read_text(encoding="utf-8")
        self.assertIn('catalogo_memos = os.getenv("catalogo_memos", "[]")', source)
        self.assertIn('catalogo_tanaka = os.getenv("catalogo_tanaka", "[]")', source)

    def test_bot_selects_catalog_prompt_and_storage_by_business(self):
        source = Path("bot.py").read_text(encoding="utf-8")
        self.assertIn("config.presaved_files_for_business()", source)
        self.assertIn("config.catalog_public_url(config.BUSINESS_CLIENT)", source)
        self.assertIn('config.BUSINESS_CLIENT / "system_instruction.txt"', source)

    def test_main_uses_business_specific_catalog_filename(self):
        source = Path("main.py").read_text(encoding="utf-8")
        self.assertIn("config.catalog_public_url(config.BUSINESS_CLIENT)", source)
        self.assertIn('f"catalogo-{config.BUSINESS_CLIENT}-{digest}.pdf"', source)

    def test_memos_instruction_exists(self):
        instruction = Path("src/clients/memos/system_instruction.txt").read_text(encoding="utf-8")
        self.assertIn("Quesos Memo's", instruction)
        self.assertIn("catalogo_pdf", instruction)
        self.assertIn("$400.000", instruction)


if __name__ == "__main__":
    unittest.main()
