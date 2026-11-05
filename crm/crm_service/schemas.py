from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CRMUpdate(Input):
    version: int = Field(ge=1)
    stage: str | None = Field(default=None, min_length=1, max_length=80)
    assigned_to: str | None = Field(default=None, max_length=200)
    tags: list[str] | None = Field(default=None, max_length=30)

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, tags: list[str] | None) -> list[str] | None:
        if tags is not None and any(not t.strip() or len(t) > 80 for t in tags):
            raise ValueError("Tags must contain 1 to 80 characters")
        return sorted(set(t.strip() for t in tags)) if tags is not None else None


class NoteInput(Input):
    body: str = Field(min_length=1, max_length=20000)


class TaskInput(Input):
    title: str = Field(min_length=1, max_length=1000)
    due_at: datetime | None = None
    assigned_to: str | None = Field(default=None, max_length=200)
    completed: bool = False

    @field_validator("due_at")
    @classmethod
    def aware_date(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("due_at must include timezone")
        return value


class TaskUpdate(Input):
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    due_at: datetime | None = None
    assigned_to: str | None = Field(default=None, max_length=200)
    completed: bool | None = None

    @field_validator("due_at")
    @classmethod
    def aware_date(cls, value: datetime | None) -> datetime | None:
        return TaskInput.aware_date(value)


class ReadInput(Input):
    read: bool = True


class SyncInput(Input):
    business_id: str = Field(min_length=1, max_length=63)
    idempotency_key: str = Field(min_length=1, max_length=100)


class ControlInput(Input):
    command_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    expected_version: int = Field(ge=0)
    reason: str = Field(default="Operator action", min_length=1, max_length=255)


class HistoricalDismissInput(Input):
    command_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    version: int = Field(ge=1)


class HumanMessageInput(Input):
    command_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    expected_version: int = Field(ge=0)
    text: str = Field(default="", max_length=4096)
    attachment_id: str | None = None

    @field_validator("attachment_id")
    @classmethod
    def valid_attachment(cls, value: str | None) -> str | None:
        return str(UUID(value)) if value is not None else None

    @model_validator(mode="after")
    def content_required(self) -> "HumanMessageInput":
        if not self.text and not self.attachment_id:
            raise ValueError("A message requires text or an attachment")
        return self


class PushKeys(Input):
    p256dh: str = Field(min_length=80, max_length=200, pattern=r"^[A-Za-z0-9_-]+=*$")
    auth: str = Field(min_length=20, max_length=100, pattern=r"^[A-Za-z0-9_-]+=*$")


class PushInput(Input):
    endpoint: str = Field(max_length=4096)
    keys: PushKeys


class PreferencesInput(Input):
    business_id: str
    in_app: bool = True
    web_push: bool = True


Bucket = Literal["awaiting_human", "human_active", "ai_active", "closed", "all"]
