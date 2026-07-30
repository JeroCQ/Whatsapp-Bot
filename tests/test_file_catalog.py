import unittest

from file_catalog import catalog_prompt, load_file_catalog


class FileCatalogTests(unittest.TestCase):
    def test_loads_link_and_builds_model_instructions(self):
        catalog = load_file_catalog('[{"id":"catalogo_pdf","description":"Enviar al pedir catálogo","type":"document","link":"https://cdn.example/catalog.pdf","filename":"catalog.pdf"}]')
        self.assertEqual(catalog["catalogo_pdf"].filename, "catalog.pdf")
        self.assertIn("catalogo_pdf", catalog_prompt(catalog))
        self.assertNotIn("https://cdn.example", catalog_prompt(catalog))

    def test_rejects_unsafe_or_ambiguous_sources(self):
        with self.assertRaises(ValueError):
            load_file_catalog('[{"id":"x","description":"x","link":"http://example.com/x"}]')
        with self.assertRaises(ValueError):
            load_file_catalog('[{"id":"x","description":"x","link":"https://example.com/x","media_id":"1"}]')

    def test_empty_configuration_disables_file_requests(self):
        self.assertEqual(load_file_catalog(""), {})
        self.assertIn("requested_files=[]", catalog_prompt({}))


if __name__ == "__main__":
    unittest.main()
