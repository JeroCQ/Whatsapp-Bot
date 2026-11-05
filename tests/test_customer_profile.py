import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture
def profiles():
    customers = {
        "573001": {"phone_number": "573001", "name": "Cliente"},
        "573002": {"phone_number": "573002", "name": "Otro"},
    }
    states = {phone: {"phone_number": phone, "is_paused": True} for phone in customers}

    class Query:
        def __init__(self, table):
            self.rows = customers if table == "customers" else states
            self.filters, self.values, self.mode = {}, {}, "select"
            self.placeholder = False

        def upsert(self, values, **kwargs):
            self.values, self.mode = values, "insert_ignore"
            assert kwargs["ignore_duplicates"]
            return self

        def update(self, values):
            self.values, self.mode = values, "update"
            return self

        def eq(self, key, value):
            self.filters[key] = value
            return self

        def select(self, _columns):
            return self

        def or_(self, expression):
            assert 'name.eq.""' in expression and "name.ilike.cliente" in expression
            self.placeholder = True
            return self

        def execute(self):
            if self.mode == "insert_ignore":
                self.rows.setdefault(self.values["phone_number"], dict(self.values))
            selected = [
                row
                for row in self.rows.values()
                if all(row.get(k) == v for k, v in self.filters.items())
            ]
            if self.mode == "update":
                for row in selected:
                    if self.placeholder and str(row.get("name") or "").casefold() in {
                        "",
                        "cliente",
                        "client",
                        "customer",
                    }:
                        row.update(self.values)
            return SimpleNamespace(data=selected)

    config, supabase, observation = (
        ModuleType(name) for name in ("config", "supabase", "observability")
    )
    config.config = SimpleNamespace(
        SUPABASE_URL="https://synthetic.example", SUPABASE_KEY="synthetic"
    )
    supabase.Client = object
    supabase.create_client = lambda *_: SimpleNamespace(table=Query)
    observation.message_metadata = lambda *_: {}
    observation.message_event_key = lambda *_: None
    spec = importlib.util.spec_from_file_location(
        "profile_database_test", Path(__file__).parents[1] / "database.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {"config": config, "supabase": supabase, "observability": observation},
    ):
        spec.loader.exec_module(module)
    return module, customers


@pytest.mark.parametrize("old", [None, "", "Cliente", "Client", "customer"])
def test_real_meta_profile_replaces_only_placeholder(profiles, old):
    database, rows = profiles
    rows["573001"]["name"] = old
    assert database.get_or_create_customer_state("573001", "  Nombre de WhatsApp  ")[
        "is_paused"
    ]
    assert rows["573001"]["name"] == "Nombre de WhatsApp"
    assert rows["573002"]["name"] == "Otro"


def test_recovery_default_does_not_erase_existing_profile(profiles):
    database, rows = profiles
    rows["573001"]["name"] = "Nombre existente"
    database.get_or_create_customer_state("573001")
    database.get_or_create_customer_state("573001", "Otro nombre entrante")
    assert rows["573001"]["name"] == "Nombre existente"
