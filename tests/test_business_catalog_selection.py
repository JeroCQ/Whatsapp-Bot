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


if __name__ == "__main__":
    unittest.main()
