"""Authenticated, GET-only exports owned by each bot; no caller-selected SQL."""

import hmac
import json
import re

from fastapi import APIRouter, HTTPException, Query, Request, Response

SOURCE_FIELDS = {
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
        "crm_control_version",
        "crm_control_phase",
        "crm_control_owner",
    ],
    "message_logs": [
        "id",
        "phone_number",
        "role",
        "content",
        "created_at",
        "metadata",
        "event_key",
    ],
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
SOURCE_REQUIRED = {
    "customers": {"phone_number"},
    "conversation_states": {"phone_number"},
    "message_logs": {"id", "phone_number", "created_at", "role"},
    "handoff_deliveries": {"business_id", "phone_number"},
}


class SupabaseSourceReader:
    def __init__(self, client, business_id, provider):
        self.client, self.business_id, self.provider = client, business_id, provider

    def page(self, table, *, after, limit):
        if table not in SOURCE_FIELDS:
            raise ValueError("Unsupported source table")
        fields, warnings = list(SOURCE_FIELDS[table]), []
        key = "id" if table == "message_logs" else "phone_number"
        for _ in range(len(fields) + 1):
            query = (
                self.client.table(table)
                .select(",".join(fields))
                .order(key)
                .limit(limit)
            )
            if after is not None:
                query = query.gt(key, after)
            if table == "handoff_deliveries":
                query = query.eq("business_id", self.business_id)
            try:
                rows = query.execute().data or []
            except Exception as error:
                code = getattr(error, "code", "")
                if code in {"42P01", "PGRST205"} and table != "message_logs":
                    return [], [f"missing_table:{table}"]
                message = str(getattr(error, "message", ""))
                missing = next(
                    (
                        field
                        for field in fields
                        if re.search(
                            rf"(?:[.\s\"']){re.escape(field)}(?:[\s\"']|$)", message
                        )
                    ),
                    None,
                )
                if (
                    code in {"42703", "PGRST204"}
                    and missing
                    and missing not in SOURCE_REQUIRED[table]
                ):
                    fields.remove(missing)
                    warnings.append(f"missing_column:{table}.{missing}")
                    continue
                raise RuntimeError("Source export unavailable") from None
            if table == "conversation_states":
                rows = [
                    {**row, "human_support_provider": self.provider} for row in rows
                ]
            return rows, warnings
        raise RuntimeError("Source schema incompatible")


def create_source_router(settings, reader_factory):
    router = APIRouter()

    @router.get("/internal/crm/v1/businesses/{business_id}/source/{table}")
    def export_page(
        business_id: str,
        table: str,
        request: Request,
        response: Response,
        after: str | None = Query(None, max_length=200),
        limit: int = Query(500, ge=1, le=1000),
    ):
        if not getattr(settings, "CRM_SOURCE_READ_ENABLED", False):
            raise HTTPException(404, "Source export is disabled")
        if request.headers.get("origin") or request.headers.get("sec-fetch-site"):
            raise HTTPException(403, "Server-to-server export only")
        secret = getattr(settings, "CRM_SOURCE_READ_SECRET", "") or ""
        if len(secret) < 32 or not hmac.compare_digest(
            request.headers.get("x-crm-source-secret", ""), secret
        ):
            raise HTTPException(401, "Source authentication required")
        if business_id != settings.BUSINESS_ID:
            raise HTTPException(403, "Business scope mismatch")
        if table not in SOURCE_FIELDS:
            raise HTTPException(404, "Source table not available")
        if table == "message_logs" and after is not None:
            if not after.isdigit():
                raise HTTPException(
                    422, "Message cursor must be a non-negative integer"
                )
            after = int(after)
        try:
            rows, warnings = reader_factory().page(table, after=after, limit=limit)
        except Exception:
            raise HTTPException(503, "Source export unavailable") from None
        response.headers["X-CRM-Data-Quality"] = json.dumps(
            warnings, separators=(",", ":")
        )
        return rows

    return router
