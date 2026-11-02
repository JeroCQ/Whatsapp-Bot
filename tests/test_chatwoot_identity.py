"""Phone and opaque WhatsApp contacts stay compatible and deployment scoped."""

import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import patch

import pytest


@pytest.fixture
def chatwoot_api():
    """Load this module with private settings without importing application config."""
    private_config = types.ModuleType("config")
    private_config.config = types.SimpleNamespace(
        BUSINESS_ID="identity_test",
        WA_PHONE_NUMBER_ID="test-meta-number",
        WA_TOKEN="test-wa-token",
        CHATWOOT_BASE_URL="https://chatwoot.example",
        CHATWOOT_ACCOUNT_ID="10",
        CHATWOOT_INBOX_ID="20",
        CHATWOOT_API_TOKEN="test-chatwoot-token",
    )
    path = Path(__file__).resolve().parents[1] / "chatwoot_api.py"
    spec = importlib.util.spec_from_file_location("_chatwoot_identity_test_module", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"config": private_config}):
        spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self, body, status_code=200):
        self.body = body
        self.status_code = status_code

    def json(self):
        return self.body


@pytest.fixture
def contact_api(monkeypatch, chatwoot_api):
    calls = {"search": [], "create": [], "update": [], "results": []}
    monkeypatch.setattr(chatwoot_api.config, "BUSINESS_ID", "identity_test")
    monkeypatch.setattr(chatwoot_api.config, "WA_PHONE_NUMBER_ID", "test-meta-number")
    monkeypatch.setattr(chatwoot_api.config, "CHATWOOT_BASE_URL", "https://chatwoot.example")
    monkeypatch.setattr(chatwoot_api.config, "CHATWOOT_ACCOUNT_ID", "10")
    monkeypatch.setattr(chatwoot_api.config, "CHATWOOT_INBOX_ID", "20")
    monkeypatch.setattr(chatwoot_api.config, "CHATWOOT_API_TOKEN", "test-chatwoot-token")

    def search(url, **kwargs):
        calls["search"].append((url, kwargs))
        return Response({"payload": calls["results"]})

    def create(url, **kwargs):
        calls["create"].append((url, kwargs))
        return Response({"payload": {"contact": {"id": 31}}}, 201)

    def update(url, **kwargs):
        calls["update"].append((url, kwargs))
        return Response({"id": 32})

    monkeypatch.setattr(chatwoot_api, "get", search)
    monkeypatch.setattr(chatwoot_api, "post", create)
    monkeypatch.setattr(chatwoot_api, "put", update)
    return calls


@pytest.mark.parametrize("business_id", ["tanaka", "memos", "velvet", "future_business"])
@pytest.mark.parametrize("sender", ["CO.TestOpaque123", "CO.ENT.TestParent456"])
def test_opaque_contact_has_stable_scoped_identifier_and_no_phone(contact_api, monkeypatch, chatwoot_api, business_id, sender):
    monkeypatch.setattr(chatwoot_api.config, "BUSINESS_ID", business_id)

    assert chatwoot_api.get_or_create_contact(sender, "Test Customer") == 31
    first_data = contact_api["create"][-1][1]["json"]
    assert first_data["inbox_id"] == 20
    assert first_data["name"] == "Test Customer"
    assert "phone_number" not in first_data
    assert 0 < len(first_data["identifier"]) <= 255
    assert contact_api["search"][-1][1]["params"] == {"q": first_data["identifier"]}

    assert chatwoot_api.get_or_create_contact(sender, "New Display Name") == 31
    assert contact_api["create"][-1][1]["json"]["identifier"] == first_data["identifier"]


def test_same_opaque_id_is_isolated_by_business_and_meta_number(contact_api, monkeypatch, chatwoot_api):
    identifiers = []
    for business_id in ("tanaka", "memos", "velvet", "future_business"):
        for number_id in ("test-meta-number-a", "test-meta-number-b"):
            monkeypatch.setattr(chatwoot_api.config, "BUSINESS_ID", business_id)
            monkeypatch.setattr(chatwoot_api.config, "WA_PHONE_NUMBER_ID", number_id)
            chatwoot_api.get_or_create_contact("CO.TestOpaque123")
            identifiers.append(contact_api["create"][-1][1]["json"]["identifier"])

    assert len(set(identifiers)) == 8


def test_opaque_lookup_skips_partial_match_and_updates_exact_contact(contact_api, chatwoot_api):
    sender = "CO.TestOpaque123"
    chatwoot_api.get_or_create_contact(sender)
    identifier = contact_api["create"][-1][1]["json"]["identifier"]
    contact_api["create"].clear()
    contact_api["results"] = [
        {"id": 99, "identifier": f"{identifier}-other", "name": "Other Customer"},
        {"id": 32, "identifier": identifier, "name": "Previous Name"},
    ]

    assert chatwoot_api.get_or_create_contact(sender, "Updated Name") == 32

    assert contact_api["create"] == []
    assert len(contact_api["update"]) == 1
    url, kwargs = contact_api["update"][0]
    assert url.endswith("/contacts/32")
    assert kwargs["json"] == {"name": "Updated Name"}


def test_partial_opaque_identifier_never_selects_another_contact(contact_api, chatwoot_api):
    sender = "CO.TestOpaque123"
    chatwoot_api.get_or_create_contact(sender)
    identifier = contact_api["create"][-1][1]["json"]["identifier"]
    contact_api["create"].clear()
    contact_api["results"] = [
        {"id": 99, "identifier": f"{identifier}-other", "name": "Other Customer"},
        {"id": 98, "identifier": "different-identity", "name": identifier},
    ]

    assert chatwoot_api.get_or_create_contact(sender, "Test Customer") == 31

    assert contact_api["update"] == []
    assert len(contact_api["create"]) == 1
    assert contact_api["create"][0][1]["json"]["identifier"] == identifier


def test_existing_numeric_contact_keeps_legacy_phone_lookup(contact_api, chatwoot_api):
    phone = "12025550123"  # Reserved NANPA example number, never a real customer.
    contact_api["results"] = [{"id": 32, "phone_number": f"+{phone}", "name": "Test Customer"}]

    assert chatwoot_api.get_or_create_contact(phone, "Test Customer") == 32

    assert contact_api["search"][0][1]["params"] == {"q": phone}
    assert contact_api["create"] == []
    assert contact_api["update"] == []


@pytest.mark.parametrize("phone", ["12025550123", "+12025550123"])
def test_new_numeric_contact_keeps_legacy_phone_payload(contact_api, chatwoot_api, phone):
    assert chatwoot_api.get_or_create_contact(phone, "Test Customer") == 31

    data = contact_api["create"][0][1]["json"]
    assert data == {"inbox_id": 20, "name": "Test Customer", "phone_number": "+12025550123"}


def test_long_parent_identity_is_stable_within_chatwoot_identifier_limit(contact_api, monkeypatch, chatwoot_api):
    monkeypatch.setattr(chatwoot_api.config, "BUSINESS_ID", "future_business_" + "x" * 220)
    sender = "CO.ENT." + "a" * 128

    assert chatwoot_api.get_or_create_contact(sender) == 31

    identifier = contact_api["create"][0][1]["json"]["identifier"]
    assert len(identifier) <= 255
    assert "phone_number" not in contact_api["create"][0][1]["json"]
