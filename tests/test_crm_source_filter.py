from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from crm_source_api import SupabaseSourceReader, create_source_router


def test_source_phone_filter_remains_authenticated_get_only_and_business_scoped():
    query = MagicMock()
    for operation in ("select", "order", "limit", "eq", "gt"):
        getattr(query, operation).return_value = query
    query.execute.return_value = SimpleNamespace(
        data=[{"phone_number": "573001", "is_paused": True}]
    )
    client = SimpleNamespace(table=lambda _: query)
    secret = "synthetic-source-" + "x" * 40
    settings = SimpleNamespace(
        BUSINESS_ID="future_brand",
        CRM_SOURCE_READ_ENABLED=True,
        CRM_SOURCE_READ_SECRET=secret,
    )
    app = FastAPI()
    app.include_router(
        create_source_router(
            settings, lambda: SupabaseSourceReader(client, "future_brand", "crm")
        )
    )
    with TestClient(app) as http:
        path = "/internal/crm/v1/businesses/future_brand/source/conversation_states"
        assert http.get(path, params={"phone_number": "573001"}).status_code == 401
        assert not query.execute.called
        response = http.get(
            path,
            params={"phone_number": "573001", "limit": 1},
            headers={"X-CRM-Source-Secret": secret},
        )
        assert response.status_code == 200
        assert response.headers["X-CRM-Phone-Filter"] == "1"
        assert response.json()[0]["human_support_provider"] == "crm"
        query.eq.assert_called_with("phone_number", "573001")
        assert (
            http.get(
                path.replace("future_brand", "another_brand"),
                headers={"X-CRM-Source-Secret": secret},
            ).status_code
            == 403
        )
        assert (
            http.post(path, headers={"X-CRM-Source-Secret": secret}).status_code == 405
        )
        assert query.execute.call_count == 1
