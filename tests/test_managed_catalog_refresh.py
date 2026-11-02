"""Exercise real catalog refresh against isolated synthetic database queries."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from test_chatwoot_integration import main


@pytest.fixture
def catalog_database(monkeypatch):
    import database
    from google import genai

    state = types.SimpleNamespace(rows=[], filters=[], fail=False)

    class Query:
        def __init__(self):
            self.business = None

        def select(self, _columns):
            return self

        def eq(self, column, value):
            assert column == "business_id"
            self.business = value
            state.filters.append(value)
            return self

        def order(self, _column):
            return self

        def execute(self):
            if state.fail:
                raise RuntimeError("synthetic database unavailable")
            assert self.business is not None
            return types.SimpleNamespace(data=[row for row in state.rows if row["business_id"] == self.business])

    monkeypatch.setattr(database, "supabase", types.SimpleNamespace(table=lambda _name: Query()))
    monkeypatch.setattr(main.config, "BUSINESS_ID", "memos")
    monkeypatch.setattr(main.config, "PRESAVED_FILES_JSON", "[]")
    monkeypatch.setattr(genai, "Client", lambda **kwargs: object())
    spec = importlib.util.spec_from_file_location(
        "_managed_catalog_refresh_test", Path(__file__).resolve().parents[1] / "bot.py",
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return state, module, database


@pytest.mark.parametrize("business", ["memos", "tanaka", "velvet", "future_brand"])
def test_real_catalog_refresh_tracks_isolated_add_replace_and_delete(catalog_database, monkeypatch, business):
    state, bot, _database = catalog_database
    monkeypatch.setattr(main.config, "BUSINESS_ID", business)
    own = {"business_id": business, "catalog_id": "catalogo_general", "public_name": "Versión inicial",
           "description": "Portafolio", "filename": f"{business}/catalogo_general.pdf", "media_type": "document"}
    foreign = dict(own, business_id="another_brand", catalog_id="catalogo_ajeno")
    pending = dict(own, catalog_id="catalogo_pendiente", filename=None)
    state.rows = [own, foreign, pending]
    state.filters.clear()

    first = dict(bot.refresh_managed_catalogs(strict=True))
    assert set(first) == {"catalogo_general"}
    assert first["catalogo_general"].default_caption == "Versión inicial"

    state.rows = [dict(own, public_name="Nueva versión"), foreign, pending]
    second = dict(bot.refresh_managed_catalogs(strict=True))
    assert second["catalogo_general"].default_caption == "Nueva versión"

    state.rows = [dict(own, catalog_id="catalogo_nuevo"), foreign, pending]
    third = dict(bot.refresh_managed_catalogs(strict=True))
    assert set(third) == {"catalogo_nuevo"}

    state.rows = [foreign]
    assert bot.refresh_managed_catalogs(strict=True) == {}
    assert state.filters == [business] * 4


def test_strict_command_refresh_propagates_database_failure_without_using_cached_catalog(catalog_database):
    state, bot, database = catalog_database
    bot.FILE_CATALOG["catalogo_anterior"] = object()
    state.fail = True

    with pytest.raises(RuntimeError, match="synthetic database unavailable"):
        bot.refresh_managed_catalogs(strict=True)

    # Existing runtime rolling-upgrade fallback is deliberately preserved;
    # explicit advisor commands demand the successful fresh read instead.
    assert database.get_catalog_assets() == []
