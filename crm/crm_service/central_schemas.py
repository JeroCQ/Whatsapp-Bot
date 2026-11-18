"""Allowlisted draft/usage inputs: no credentials, prompts or response bodies."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from crm_service.schemas import Input

BusinessId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")]
CorrelationId = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:@+-]+$")]
TokenCount = Annotated[int, Field(strict=True, ge=0, le=9223372036854775807)]


class DraftSettings(Input):
    # Absent keys inherit; explicit nulls are not configuration values.
    timezone: str | None = Field(default=None, max_length=80)
    locale: str | None = Field(default=None, pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")
    sync_interval: int | None = Field(default=None, strict=True, ge=5, le=3600)
    page_size: int | None = Field(default=None, strict=True, ge=1, le=1000)
    max_pages: int | None = Field(default=None, strict=True, ge=1, le=1000)
    sync_max_seconds: float | None = Field(default=None, ge=0.05, le=120, allow_inf_nan=False)
    notifications_enabled: bool | None = Field(default=None, strict=True)
    operations_enabled: bool | None = Field(default=None, strict=True)

    @field_validator("*")
    @classmethod
    def no_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Omit inherited settings instead of supplying null")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str | None) -> str | None:
        try:
            ZoneInfo(str(value))
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Unknown timezone") from None
        return value


class SharedDraftInput(Input):
    version: int = Field(strict=True, ge=0)
    values: DraftSettings


class BusinessDraftInput(Input):
    version: int = Field(strict=True, ge=0)
    display_name: str = Field(min_length=1, max_length=120)
    whatsapp_phone_number_id: str | None = Field(default=None, pattern=r"^[0-9]{1,32}$")
    whatsapp_business_account_id: str | None = Field(default=None, pattern=r"^[0-9]{1,32}$")
    overrides: DraftSettings
    operator_profiles: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=100)
    connection_purposes: list[Literal["bot_control", "source_read", "chatwoot"]] | None = None


class TurnInput(Input):
    business_id: BusinessId
    turn_id: CorrelationId
    conversation_id: str = Field(min_length=1, max_length=200)
    started_at: datetime | None = None
    outcome: Literal["unknown", "reply_accepted", "no_reply", "failed"] = "unknown"

    @field_validator("started_at")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Include a timezone")
        return value


class AttemptInput(Input):
    business_id: BusinessId
    conversation_id: str = Field(min_length=1, max_length=200)
    turn_id: CorrelationId
    attempt_id: CorrelationId
    attempt_index: int = Field(strict=True, ge=1, le=2147483647)
    purpose: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_.:-]*$")
    model_name: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$")
    model_version: str | None = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$")
    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None
    total_tokens: TokenCount | None = None
    cached_input_tokens: TokenCount | None = None
    thought_tokens: TokenCount | None = None
    latency_ms: TokenCount | None = None
    outcome: Literal["success", "failed", "timeout", "cancelled", "unknown"]
    error_type: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Include a timezone")
        return value


class SupportInput(Input):
    id: UUID
    business_id: BusinessId
    minutes: int = Field(strict=True, ge=1, le=2147483647)
    occurred_at: datetime
    category: Literal["setup", "incident", "maintenance", "other"] = "other"
    note: str = Field(default="", max_length=1000)

    @field_validator("occurred_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return AttemptInput.aware(value)
