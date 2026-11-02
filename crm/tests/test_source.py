import httpx
import pytest

from crm_service.settings import Business
from crm_service.source import ReadOnlyClient, SourceAdapter, SourceFailure

BIZ = Business("future_brand", "https://future.supabase.co", "synthetic-key")


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE", "OPTIONS", "CONNECT", "TRACE"])
def test_source_writes_blocked_before_transport(method):
    sent = []
    with ReadOnlyClient(BIZ, httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200))) as client:
        with pytest.raises(SourceFailure, match="source_method_forbidden"):
            client.request(method, "/rest/v1/customers")
    assert sent == []


@pytest.mark.parametrize(
    "path",
    [
        "/rest/v1/rpc/do_something",
        "https://evil.example/rest/v1/customers",
        "/rest/v1/customers/../rpc/write",
        "/rest/v1/not_allowed",
    ],
)
def test_rpc_and_other_origins_blocked(path):
    with ReadOnlyClient(
        BIZ, httpx.MockTransport(lambda _: pytest.fail("must not reach transport"))
    ) as client:
        with pytest.raises(SourceFailure):
            client.get(path)


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_allowed_methods(method):
    with ReadOnlyClient(BIZ, httpx.MockTransport(lambda _: httpx.Response(200, json=[]))) as client:
        assert client.request(method, "/rest/v1/customers").status_code == 200


def test_missing_optional_column_degrades_with_warning():
    sent = []

    def handler(request):
        sent.append(request)
        if "metadata" in request.url.params["select"]:
            return httpx.Response(
                400, json={"code": "42703", "message": "column message_logs.metadata does not exist"}
            )
        return httpx.Response(200, json=[])

    adapter = SourceAdapter(BIZ, httpx.MockTransport(handler))
    assert adapter.page("message_logs", after=3, limit=20) == []
    assert len(sent) == 2
    assert "missing_column:message_logs.metadata" in adapter.warnings
    assert sent[-1].url.params["id"] == "gt.3"
    adapter.close()


def test_missing_optional_table_degrades():
    adapter = SourceAdapter(
        BIZ, httpx.MockTransport(lambda _: httpx.Response(404, json={"code": "PGRST205"}))
    )
    assert adapter.page("handoff_deliveries", after=None, limit=20) == []
    assert "missing_table:handoff_deliveries" in adapter.warnings
    adapter.close()


def test_redirect_does_not_forward_credentials():
    calls = []
    adapter = SourceAdapter(
        BIZ,
        httpx.MockTransport(
            lambda r: calls.append(r) or httpx.Response(302, headers={"location": "https://evil.example"})
        ),
    )
    with pytest.raises(SourceFailure, match="source_http_302"):
        adapter.page("customers", after=None, limit=20)
    assert len(calls) == 1
    adapter.close()
