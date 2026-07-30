import json
import unittest

from file_catalog import catalog_for_prompt, load_file_catalog


class FileCatalogTests(unittest.TestCase):
    def test_loads_and_infers_supported_files(self):
        catalog = load_file_catalog(json.dumps([
            {"id": "catalog", "url": "https://cdn.example/catalog.pdf", "description": "Prices"},
            {"id": "photo", "url": "https://cdn.example/photo.jpg"},
        ]))
        self.assertEqual(catalog["catalog"].media_type, "document")
        self.assertEqual(catalog["photo"].media_type, "image")
        self.assertIn("ID `catalog`: Prices", catalog_for_prompt(catalog))

    def test_rejects_non_https_and_incomplete_entries(self):
        catalog = load_file_catalog(json.dumps([
            {"id": "local", "url": "file:///secret.pdf"},
            {"id": "http", "url": "http://example.com/file.pdf"},
            {"url": "https://example.com/no-id.pdf"},
        ]))
        self.assertEqual(catalog, {})

    def test_invalid_shape_has_clear_error(self):
        with self.assertRaisesRegex(ValueError, "JSON array"):
            load_file_catalog("{}")


if __name__ == "__main__":
    unittest.main()
