import unittest

from file_catalog import catalog_prompt, extend_system_instruction, load_file_catalog, merge_managed_catalogs


def test_managed_catalog_without_file_is_not_exposed_to_ai():
    catalog = load_file_catalog(
        '[{"id":"catalogo_vacio","description":"old","type":"document"}]'
    )
    rows = [{
        "catalog_id": "catalogo_vacio",
        "public_name": "Vacío",
        "description": "No usar todavía",
        "media_type": "document",
        "filename": None,
    }]

    assert merge_managed_catalogs(catalog, rows, lambda file_id: f"https://files.test/{file_id}") == {}


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

    def test_dashboard_managed_catalog_does_not_need_duplicate_link(self):
        catalog = load_file_catalog(
            '[{"id":"catalogo_pdf","description":"Catálogo Tanaka","type":"document","filename":"Catalogo_Tanaka.pdf"}]'
        )

        self.assertIsNone(catalog["catalogo_pdf"].link)
        self.assertIsNone(catalog["catalogo_pdf"].media_id)

    def test_exact_railway_catalog_value_without_link_is_valid(self):
        raw_value = (
            '[{"id":"catalogo_pdf","description":"Catálogo de Tanaka Saludable; '
            'enviarlo cuando pidan el catálogo, precios generales o quieran ver todos los productos.",'
            '"type":"document","filename":"Catalogo_Tanaka.pdf",'
            '"caption":"Aquí tienes nuestro catálogo completo ☺️"}]'
        )

        catalog = load_file_catalog(raw_value)

        self.assertEqual(catalog["catalogo_pdf"].filename, "Catalogo_Tanaka.pdf")

    def test_non_catalog_file_still_requires_a_source(self):
        with self.assertRaises(ValueError):
            load_file_catalog('[{"id":"ficha_tecnica","description":"Ficha"}]')

    def test_empty_configuration_disables_file_requests(self):
        self.assertEqual(load_file_catalog(""), {})
        self.assertIn("requested_files=[]", catalog_prompt({}))

    def test_file_rules_are_appended_without_changing_business_prompt(self):
        original = "PERSONALIDAD\nPRECIOS\nHANDOFF"
        catalog = load_file_catalog('[{"id":"catalogo_pdf","description":"Catálogo","media_id":"123"}]')
        extended = extend_system_instruction(original, catalog)
        self.assertTrue(extended.startswith(original + "\n\n"))
        self.assertEqual(extended[:len(original)], original)
        self.assertIn("catalogo_pdf", extended)


if __name__ == "__main__":
    unittest.main()
