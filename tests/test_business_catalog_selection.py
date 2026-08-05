"""Regression checks for keeping each business catalog configuration independent."""

import ast
from pathlib import Path
import unittest


class BusinessCatalogSelectionTests(unittest.TestCase):
    def test_tanaka_catalog_is_declared_without_removing_memos_catalog(self):
        source = Path("config.py").read_text(encoding="utf-8")

        self.assertIn('catalogo_memos = os.getenv("catalogo_memos", "[]")', source)
        self.assertIn('catalogo_tanaka = os.getenv("catalogo_tanaka", "[]")', source)

    def test_bot_loads_only_tanaka_catalog(self):
        tree = ast.parse(Path("bot.py").read_text(encoding="utf-8"))
        assignment = next(
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "FILE_CATALOG" for target in node.targets)
        )

        self.assertEqual(
            ast.unparse(assignment.value),
            "load_file_catalog(config.catalogo_tanaka, 'catalogo_tanaka')",
        )


class DashboardCatalogUrlTests(unittest.TestCase):
    def test_bot_overrides_catalog_pdf_link_with_storage_url(self):
        source = Path("bot.py").read_text(encoding="utf-8")

        self.assertIn('FILE_CATALOG["catalogo_pdf"] = replace(', source)
        self.assertIn('link=config.catalog_public_url("tanaka")', source)
        self.assertIn('media_id=None', source)

    def test_main_cache_busts_dashboard_catalog_link_for_meta(self):
        source = Path("main.py").read_text(encoding="utf-8")

        self.assertIn("def catalog_link_for_whatsapp", source)
        self.assertIn('query["v"]', source)
        self.assertIn('catalog_link_for_whatsapp(file_id, item.link)', source)
        self.assertIn('resolved_filename = f"catalogo-tanaka-', source)


if __name__ == "__main__":
    unittest.main()
