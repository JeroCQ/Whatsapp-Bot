from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SPEC = spec_from_file_location("recover_catalog_deliveries", Path(__file__).parents[1] / "scripts" / "recover_catalog_deliveries.py")
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_recovery_payload_selects_only_requested_phones():
    rows = [
        {"phone_number": "573111111111", "catalog_id": "catalogo_uno"},
        {"phone_number": "573222222222", "catalog_id": "catalogo_dos"},
    ]
    assert MODULE.recovery_payload(rows, {"573222222222"}, False) == [
        {"phone_number": "573222222222", "catalog_id": "catalogo_dos"},
    ]
    assert MODULE.recovery_payload(rows, set(), True) == rows
