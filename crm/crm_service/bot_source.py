"""Supported real-source mode: authenticated read-only bot export, no Supabase key."""

import json
from typing import Any
from urllib.parse import quote

import httpx

from crm_service.budget import ReadBudget
from crm_service.settings import Business, bot_origin
from crm_service.source import FIELDS, SourceFailure


class ReadOnlyBotClient(httpx.Client):
    def __init__(self, *, origin: str, business_id: str, **kwargs: Any):
        self.origin = httpx.URL(origin)
        self.allowed_paths = {
            f"/internal/crm/v1/businesses/{quote(business_id, safe='')}/source/{table}" for table in FIELDS
        }
        super().__init__(base_url=origin, **kwargs)

    def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        if request.method not in {"GET", "HEAD"}:
            raise SourceFailure("source_method_forbidden")
        target = request.url
        if (target.scheme, target.host, target.port) != (
            self.origin.scheme,
            self.origin.host,
            self.origin.port,
        ) or target.path not in self.allowed_paths:
            raise SourceFailure("source_destination_forbidden")
        return super().send(request, **kwargs)


class BotSourceAdapter:
    def __init__(
        self,
        business: Business,
        budget: ReadBudget | None = None,
        transport: httpx.BaseTransport | None = None,
        *,
        test_mode: bool = False,
    ):
        if not business.bot_api_enabled or len(business.bot_source_secret) < 32:
            raise SourceFailure("bot_source_not_enabled")
        try:
            origin = bot_origin(business.bot_api_url, test_mode=test_mode)
        except ValueError:
            raise SourceFailure("invalid_bot_source_origin") from None
        self.business, self.budget = business, budget
        self.warnings: set[str] = set()
        self.client = ReadOnlyBotClient(
            origin=origin,
            business_id=business.id,
            transport=transport,
            follow_redirects=False,
            timeout=5,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            headers={"X-CRM-Source-Secret": business.bot_source_secret},
        )

    def close(self) -> None:
        self.client.close()

    def page(self, table: str, *, after: str | int | None, limit: int) -> list[dict[str, Any]]:
        if table not in FIELDS or not 1 <= limit <= 1000:
            raise SourceFailure("invalid_bot_source_page")
        params = {"limit": str(limit)}
        if after is not None:
            params["after"] = str(after)
        path = f"/internal/crm/v1/businesses/{quote(self.business.id, safe='')}/source/{table}"
        try:
            if self.budget:
                self.budget.connections_peak = 1
                with self.budget.request():
                    response = self.client.get(
                        path, params=params, timeout=min(5, self.budget.timeout_seconds())
                    )
            else:
                response = self.client.get(path, params=params)
        except httpx.HTTPError:
            raise SourceFailure("bot_source_network_error") from None
        if response.status_code != 200:
            raise SourceFailure(f"bot_source_http_{response.status_code}")
        try:
            result = response.json()
        except ValueError:
            raise SourceFailure("bot_source_invalid_response") from None
        rows = result.get("items") if isinstance(result, dict) else result
        warnings = result.get("data_quality_warnings", []) if isinstance(result, dict) else []
        if "X-CRM-Data-Quality" in response.headers:
            try:
                warnings = json.loads(response.headers["X-CRM-Data-Quality"])
            except ValueError:
                raise SourceFailure("bot_source_invalid_quality_header") from None
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SourceFailure("bot_source_invalid_page")
        if isinstance(result, dict) and result.get("business_id") != self.business.id:
            raise SourceFailure("bot_source_business_mismatch")
        if isinstance(warnings, list):
            self.warnings.update(str(warning)[:200] for warning in warnings if isinstance(warning, str))
        if self.budget:
            self.budget.record_rows(len(rows))
        return rows
