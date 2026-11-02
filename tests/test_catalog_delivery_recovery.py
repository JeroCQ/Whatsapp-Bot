from datetime import datetime, timezone

from catalog_delivery_recovery import find_catalog_recoveries


def test_finds_only_unresolved_failures_and_marks_service_window():
    rows = [
        {"phone_number": "1", "role": "user", "content": "catálogo", "created_at": "2026-09-05T10:00:00Z"},
        {"phone_number": "1", "role": "system", "content": "ERROR enviando archivos: catalogo_pdf", "created_at": "2026-09-05T10:01:00Z"},
        {"phone_number": "2", "role": "user", "content": "catálogo", "created_at": "2026-09-01T10:00:00Z"},
        {"phone_number": "2", "role": "system", "content": "ERROR enviando archivos: catalogo_otro", "created_at": "2026-09-01T10:01:00Z"},
        {"phone_number": "3", "role": "system", "content": "ERROR enviando archivos: catalogo_pdf", "created_at": "2026-09-05T09:00:00Z"},
        {"phone_number": "3", "role": "system", "content": "Archivos enviados: catalogo_pdf", "created_at": "2026-09-05T09:02:00Z"},
    ]

    found = find_catalog_recoveries(rows, datetime(2026, 9, 5, 12, tzinfo=timezone.utc))

    assert [(item.phone_number, item.catalog_id, item.within_service_window) for item in found] == [
        ("1", "catalogo_pdf", True),
        ("2", "catalogo_otro", False),
    ]
