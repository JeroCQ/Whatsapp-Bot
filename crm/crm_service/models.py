from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(timezone.utc)


def uid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class SourceConversation(Base):
    __tablename__ = "source_conversations"
    business_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(200), primary_key=True)
    name: Mapped[str] = mapped_column(Text, default="")
    customer: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    handoff: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)


class SourceMessage(Base):
    __tablename__ = "source_messages"
    business_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(200), index=True)
    role: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    message_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    event_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "phone_number"],
            ["source_conversations.business_id", "source_conversations.phone_number"],
        ),
        Index("uq_source_event", "business_id", "event_key", unique=True),
        Index("ix_source_timeline", "business_id", "phone_number", "created_at", "source_id"),
    )


class SyncCursor(Base):
    __tablename__ = "sync_cursors"
    business_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    resource: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SyncStatus(Base):
    __tablename__ = "sync_status"
    business_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    baseline_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)


class CRMConversation(Base):
    __tablename__ = "crm_conversations"
    business_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(200), primary_key=True)
    bucket: Mapped[str] = mapped_column(String(30), index=True)
    stage: Mapped[str] = mapped_column(String(80), default="new", index=True)
    assigned_to: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    classification: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sort_key: Mapped[str] = mapped_column(Text, index=True)
    transition: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "phone_number"],
            ["source_conversations.business_id", "source_conversations.phone_number"],
        ),
    )


class Agent(Base):
    __tablename__ = "crm_agents"
    profile: Mapped[str] = mapped_column(String(200), primary_key=True)
    allowed_businesses: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ReadState(Base):
    __tablename__ = "crm_read_states"
    business_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(200), primary_key=True)
    profile: Mapped[str] = mapped_column(ForeignKey("crm_agents.profile"), primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_message_id: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "phone_number"],
            ["source_conversations.business_id", "source_conversations.phone_number"],
        ),
    )


class Note(Base):
    __tablename__ = "crm_notes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    business_id: Mapped[str] = mapped_column(String(63), index=True)
    phone_number: Mapped[str] = mapped_column(String(200))
    profile: Mapped[str] = mapped_column(ForeignKey("crm_agents.profile"))
    body: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "phone_number"],
            ["source_conversations.business_id", "source_conversations.phone_number"],
        ),
    )


class Task(Base):
    __tablename__ = "crm_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    business_id: Mapped[str] = mapped_column(String(63), index=True)
    phone_number: Mapped[str] = mapped_column(String(200))
    profile: Mapped[str] = mapped_column(ForeignKey("crm_agents.profile"))
    title: Mapped[str] = mapped_column(Text)
    assigned_to: Mapped[str | None] = mapped_column(String(200), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "phone_number"],
            ["source_conversations.business_id", "source_conversations.phone_number"],
        ),
    )


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    profile: Mapped[str] = mapped_column(ForeignKey("crm_agents.profile"), index=True)
    endpoint_hash: Mapped[str] = mapped_column(String(64), unique=True)
    subscription: Mapped[dict[str, Any]] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class NotificationEvent(Base):
    __tablename__ = "notification_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    business_id: Mapped[str] = mapped_column(String(63), index=True)
    phone_number: Mapped[str] = mapped_column(String(200))
    transition: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    link: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        Index("uq_attention_transition", "business_id", "phone_number", "transition", unique=True),
        ForeignKeyConstraint(
            ["business_id", "phone_number"],
            ["source_conversations.business_id", "source_conversations.phone_number"],
        ),
    )


class PushDelivery(Base):
    __tablename__ = "push_deliveries"
    event_id: Mapped[str] = mapped_column(ForeignKey("notification_events.id"), primary_key=True)
    subscription_id: Mapped[str] = mapped_column(ForeignKey("push_subscriptions.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    business_id: Mapped[str] = mapped_column(String(63), index=True)
    phone_number: Mapped[str] = mapped_column(String(200))
    profile: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
