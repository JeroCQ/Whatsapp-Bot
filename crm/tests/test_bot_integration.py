"""Security contracts for the supported pilot source and command transport."""

from dataclasses import replace

import httpx
import pytest

from crm_service.bot_gateway import BotGateway, BotGatewayError
from crm_service.bot_source import BotSourceAdapter
from crm_service.settings import Business, Settings
from crm_service.source import SourceFailure


@pytest.fixture
def business():
    return Business(
        "pilot_alpha",
        "",
        "",
        bot_api_url="https://pilot.example",
        bot_control_secret="synthetic-control-" + "a" * 40,
        bot_source_secret="synthetic-source-" + "b" * 40,
        operations_enabled=True,
        bot_api_enabled=True,
    )


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE", "OPTIONS", "TRACE"])
def test_source_transport_cannot_write_even_if_called_directly(business, method):
    calls = []
    reader = BotSourceAdapter(business, transport=httpx.MockTransport(lambda request: calls.append(request)))
    try:
        with pytest.raises(SourceFailure, match="source_method_forbidden"):
            reader.client.request(method, f"/internal/crm/v1/businesses/{business.id}/source/customers")
        assert not calls
    finally:
        reader.close()


def test_source_secret_has_no_command_or_other_tenant_destination(business):
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["X-CRM-Source-Secret"] == business.bot_source_secret
        assert "X-CRM-Bot-Secret" not in request.headers and "apikey" not in request.headers
        return httpx.Response(
            200, json=[], headers={"X-CRM-Data-Quality": '["missing_table:handoff_deliveries"]'}
        )

    reader = BotSourceAdapter(business, transport=httpx.MockTransport(respond))
    try:
        assert reader.page("customers", after="same-contact", limit=1) == []
        assert reader.warnings == {"missing_table:handoff_deliveries"}
        for destination in (
            "/internal/crm/v1/businesses/pilot_beta/source/customers",
            "/internal/crm/v1/businesses/pilot_alpha/conversations/contact/control",
            "https://other.example/internal/crm/v1/businesses/pilot_alpha/source/customers",
            "/rest/v1/rpc/unsafe",
        ):
            with pytest.raises(SourceFailure, match="source_destination_forbidden"):
                reader.client.get(destination)
        assert len(calls) == 1
    finally:
        reader.close()


def test_gateway_never_retries_ambiguous_post_or_follows_redirect(business):
    calls = []

    def timeout(request):
        calls.append(request)
        assert request.headers["X-CRM-Bot-Secret"] == business.bot_control_secret
        assert "X-CRM-Source-Secret" not in request.headers
        raise httpx.ReadTimeout("synthetic", request=request)

    gateway = BotGateway(business, transport=httpx.MockTransport(timeout))
    try:
        with pytest.raises(BotGatewayError) as caught:
            gateway.execute("messages", "573000000001", {"command_id": "synthetic-send"})
        assert caught.value.ambiguous and caught.value.status_code == 202
        assert len(calls) == 1
    finally:
        gateway.close()
    gateway = BotGateway(
        business,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(307, headers={"Location": "https://other.example"})
        ),
    )
    try:
        with pytest.raises(BotGatewayError):
            gateway.control("573000000001")
    finally:
        gateway.close()


def test_gateway_rejects_an_authoritative_response_for_another_business(business):
    gateway = BotGateway(
        business,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"business_id": "pilot_beta"})
        ),
    )
    try:
        with pytest.raises(BotGatewayError, match="bot_scope_or_control_mismatch"):
            gateway.control("573000000001")
    finally:
        gateway.close()


def test_api_and_worker_load_distinct_bot_credentials(settings, business, monkeypatch):
    for key, value in {
        "CRM_DATABASE_URL": settings.database_url,
        "CRM_JWT_SECRET": settings.jwt_secret,
        "CRM_SYNC_SECRET": settings.sync_secret,
        "CRM_BUSINESSES": business.id,
        "CRM_SOURCE_MODE": "bot_api",
        "PILOT_ALPHA_BOT_API_URL": business.bot_api_url,
        "PILOT_ALPHA_HUMAN_CONTROL_ENABLED": "true",
        "PILOT_ALPHA_BOT_API_ENABLED": "true",
        "PILOT_ALPHA_BOT_CONTROL_SECRET": business.bot_control_secret,
        "PILOT_ALPHA_BOT_SOURCE_SECRET": business.bot_source_secret,
        "NOTIFICATIONS_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    api = Settings.from_env(service="api").businesses[business.id]
    worker = Settings.from_env(service="worker").businesses[business.id]
    assert api.bot_control_secret and not api.bot_source_secret
    assert worker.bot_source_secret and not worker.bot_control_secret
    for origin in (
        "https://pilot.example/path",
        "https://user:password@pilot.example",
        "http://pilot.example",
    ):
        monkeypatch.setenv("PILOT_ALPHA_BOT_API_URL", origin)
        with pytest.raises(ValueError):
            Settings.from_env(service="api")
    assert replace(business, bot_control_secret="").bot_source_secret
