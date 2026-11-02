"""Read-only PostgREST adapter. No Supabase SDK, RPC or generic write API."""

import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from crm_service.settings import Business

FIELDS = {
    "customers": ["phone_number", "name", "created_at"],
    "conversation_states": [
        "phone_number",
        "current_state",
        "is_paused",
        "handoff_reason",
        "last_interaction",
        "chatwoot_conversation_id",
        "customer_data",
        "order_summary",
    ],
    "message_logs": ["id", "phone_number", "role", "content", "created_at", "metadata", "event_key"],
    "handoff_deliveries": [
        "business_id",
        "phone_number",
        "request_id",
        "status",
        "attempts",
        "retry_at",
        "conversation_id",
        "last_error",
        "created_at",
        "updated_at",
    ],
}
REQUIRED = {
    "customers": {"phone_number"},
    "conversation_states": {"phone_number"},
    "message_logs": {"id", "phone_number", "role", "created_at"},
    "handoff_deliveries": {"phone_number", "business_id"},
}


class SourceFailure(Exception):
    """Safe failure code: never include source URL, body or credential."""


class ReadOnlyClient(httpx.Client):
    def __init__(self, business: Business, transport: httpx.BaseTransport | None = None):
        self.origin = business.source_url.rstrip("/")
        self.allowed_paths = {f"/rest/v1/{table}" for table in FIELDS}
        super().__init__(
            base_url=self.origin,
            timeout=httpx.Timeout(20, connect=5),
            follow_redirects=False,
            transport=transport,
            headers={"apikey": business.source_key, "Authorization": f"Bearer {business.source_key}"},
        )

    def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        target = urlsplit(str(request.url))
        origin = urlsplit(self.origin)
        if request.method not in {"GET", "HEAD"}:
            raise SourceFailure("source_method_forbidden")
        if (target.scheme, target.netloc) != (
            origin.scheme,
            origin.netloc,
        ) or target.path not in self.allowed_paths:
            raise SourceFailure("source_destination_forbidden")
        kwargs["follow_redirects"] = False
        return super().send(request, **kwargs)


class SourceAdapter:
    def __init__(self, business: Business, transport: httpx.BaseTransport | None = None):
        if not business.source_url or not business.source_key:
            raise SourceFailure("source_configuration_missing")
        self.business = business
        self.client = ReadOnlyClient(business, transport)
        self.columns = {table: list(fields) for table, fields in FIELDS.items()}
        self.warnings: set[str] = set()

    def close(self) -> None:
        self.client.close()

    def page(self, table: str, *, after: str | int | None, limit: int) -> list[dict[str, Any]]:
        if table not in FIELDS:
            raise SourceFailure("source_table_forbidden")
        key = "id" if table == "message_logs" else "phone_number"
        for _ in range(len(FIELDS[table]) + 1):
            params = {"select": ",".join(self.columns[table]), "order": f"{key}.asc", "limit": str(limit)}
            if after is not None:
                params[key] = f"gt.{after}"
            if table == "handoff_deliveries":
                params["business_id"] = f"eq.{self.business.id}"
            try:
                response = self.client.get(f"/rest/v1/{table}", params=params)
            except httpx.HTTPError:
                raise SourceFailure("source_network_error") from None
            if response.status_code == 200:
                rows = response.json()
                if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                    raise SourceFailure("source_invalid_page")
                return rows
            try:
                error = response.json()
            except ValueError:
                error = {}
            code = error.get("code", "")
            if code in {"42P01", "PGRST205"}:
                self.warnings.add(f"missing_table:{table}")
                if table == "message_logs":
                    raise SourceFailure("missing_message_logs")
                return []
            if code in {"42703", "PGRST204"}:
                message = str(error.get("message", ""))
                missing = next(
                    (
                        f
                        for f in self.columns[table]
                        if re.search(rf"(?:[.\s\"']){re.escape(f)}(?:[\s\"']|$)", message)
                    ),
                    None,
                )
                if missing and missing not in REQUIRED[table]:
                    self.columns[table].remove(missing)
                    self.warnings.add(f"missing_column:{table}.{missing}")
                    continue
                if missing:
                    self.warnings.add(f"missing_required_column:{table}.{missing}")
                    if table != "message_logs":
                        return []
            raise SourceFailure(f"source_http_{response.status_code}")
        raise SourceFailure("source_schema_incompatible")
