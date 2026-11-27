from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
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
    dirty: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


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
    delivery_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    command_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    delivery_revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    is_delivery_event: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "phone_number"],
            ["source_conversations.business_id", "source_conversations.phone_number"],
        ),
        Index("uq_source_event", "business_id", "event_key", unique=True),
        Index("ix_source_timeline", "business_id", "phone_number", "created_at", "source_id"),
        Index("ix_source_command", "business_id", "phone_number", "command_id"),
        Index("ix_source_provider", "business_id", "phone_number", "provider_message_id"),
        Index("ix_source_delivery_revision", "business_id", "phone_number", "delivery_revision"),
    )


class DeliveryReceipt(Base):
    """A durable receipt fact, including those arriving before the original message."""

    __tablename__ = "delivery_receipts"
    business_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    event_source_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(200))
    command_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    matched_source_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "event_source_id"], ["source_messages.business_id", "source_messages.source_id"]
        ),
        ForeignKeyConstraint(
            ["business_id", "matched_source_id"], ["source_messages.business_id", "source_messages.source_id"]
        ),
        Index("ix_receipt_command", "business_id", "phone_number", "command_id"),
        Index("ix_receipt_provider", "business_id", "phone_number", "provider_message_id"),
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
    run_state: Mapped[str] = mapped_column(String(30), default="pending")
    failures: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SyncRequest(Base):
    __tablename__ = "sync_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    business_id: Mapped[str] = mapped_column(String(63), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (Index("uq_sync_request_key", "business_id", "idempotency_key", unique=True),)


class BotCommand(Base):
    __tablename__ = "bot_commands"
    business_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    command_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(200), index=True)
    profile: Mapped[str] = mapped_column(String(200))
    operation: Mapped[str] = mapped_column(String(20))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="checking")
    response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    http_status: Mapped[int] = mapped_column(Integer, default=202)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


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


class BusinessDraft(Base):
    """Inactive administration data; never a replacement for Settings.businesses."""
    __tablename__ = "crm_business_drafts"
    business_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    whatsapp_phone_number_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    whatsapp_business_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    operator_profiles: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(ForeignKey("crm_agents.profile"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint("state = 'draft'", name="ck_business_draft_only"),
        Index("uq_draft_whatsapp_phone", "whatsapp_phone_number_id", unique=True),
    )


class SharedConfigDraft(Base):
    __tablename__ = "crm_shared_config_draft"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default="shared")
    values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(ForeignKey("crm_agents.profile"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (CheckConstraint("id = 'shared'", name="ck_single_shared_draft"),)


class PilotPublication(Base):
    """Immutable configuration snapshot; saving a draft never writes this table."""
    __tablename__ = "crm_pilot_publications"
    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_id: Mapped[str] = mapped_column(String(63))
    phone_number: Mapped[str] = mapped_column(String(32))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(ForeignKey("crm_agents.profile"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (CheckConstraint("business_id = 'briosos' AND phone_number = '573105338124'", name="ck_pilot_scope"),)


class PilotSwitch(Base):
    __tablename__ = "crm_pilot_switch"
    business_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    publication_version: Mapped[int | None] = mapped_column(ForeignKey("crm_pilot_publications.version"), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="standby")
    __table_args__ = (CheckConstraint("business_id = 'briosos' AND mode IN ('standby','shared','legacy')", name="ck_pilot_switch"),)


class BusinessConnectionReference(Base):
    """Opaque backend pointers, never credentials, URLs or browser-editable values."""
    __tablename__ = "crm_business_connection_refs"
    business_id: Mapped[str] = mapped_column(ForeignKey("crm_business_drafts.business_id"), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(32), primary_key=True)
    reference_key: Mapped[str] = mapped_column(String(200))


class UsageTurn(Base):
    __tablename__ = "crm_usage_turns"
    business_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(200))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str] = mapped_column(String(24), default="unknown")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint("outcome IN ('unknown','reply_accepted','no_reply','failed')", name="ck_usage_turn_outcome"),
        Index("ix_usage_turn_conversation", "business_id", "conversation_id"),
    )


class ModelUsageAttempt(Base):
    __tablename__ = "crm_model_usage_attempts"
    business_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(160))
    attempt_index: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(80))
    model_name: Mapped[str] = mapped_column(String(200))
    model_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    thought_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    outcome: Mapped[str] = mapped_column(String(20))
    error_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    request_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        ForeignKeyConstraint(["business_id", "turn_id"], ["crm_usage_turns.business_id", "crm_usage_turns.turn_id"]),
        CheckConstraint("attempt_index > 0", name="ck_usage_attempt_index"),
        CheckConstraint("outcome IN ('success','failed','timeout','cancelled','unknown')", name="ck_usage_attempt_outcome"),
        Index("ix_usage_attempt_business_time", "business_id", "observed_at", "attempt_id"),
    )


class SupportMinutes(Base):
    __tablename__ = "crm_support_minutes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(63), index=True)
    minutes: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    category: Mapped[str] = mapped_column(String(24))
    note: Mapped[str] = mapped_column(String(1000), default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("crm_agents.profile"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    request_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (CheckConstraint("minutes > 0", name="ck_support_minutes_positive"),)


class PromotionPermission(Base):
    __tablename__ = "promotion_permissions"
    business_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    sender_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(200), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_id: Mapped[str | None] = mapped_column(String(300))
    __table_args__ = (
        CheckConstraint("status IN ('unknown','granted','denied','revoked')", name="ck_promotion_permission_status"),
        Index("ix_promotion_permission_audience", "business_id", "sender_id", "status", "phone_number"),
    )


class PromotionPermissionEvent(Base):
    __tablename__ = "promotion_permission_events"
    business_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    sender_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    inbound_id: Mapped[str] = mapped_column(String(300), primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16))
    question_id: Mapped[str | None] = mapped_column(String(300))
    version: Mapped[str] = mapped_column(String(32))
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evidence: Mapped[str] = mapped_column(String(160))
    validated: Mapped[bool] = mapped_column(Boolean)
    __table_args__ = (
        CheckConstraint("status IN ('granted','denied','revoked')", name="ck_promotion_permission_event_status"),
        Index("ix_promotion_permission_questions", "business_id", "phone_number", "question_id",
              postgresql_where=text("NOT validated")),
        Index("ix_promotion_permission_contact_history", "business_id", "sender_id", "phone_number", "event_at"),
    )


class CustomerAnalysis(Base):
    __tablename__ = "customer_return_analyses"
    business_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    command_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(200))
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (
        CheckConstraint("status IN ('pending','processing','done','failed')", name="ck_customer_analysis_status"),
        Index("ix_customer_analysis_queue", "status", "retry_at", "released_at"),
        Index("ix_customer_analysis_history", "business_id", "phone_number", "released_at"),
    )


class CommercialEvent(Base):
    __tablename__ = "crm_commercial_events"
    business_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(200))
    order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    queue: Mapped[str | None] = mapped_column(String(12), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    amount_cop: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    product_subtotal_cop: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    items: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (
        Index("ix_commercial_event_period", "business_id", "occurred_at", "source_id"),
        Index("ix_commercial_event_contact", "business_id", "phone_number", "source_id"),
        Index("ix_commercial_event_order", "business_id", "order_id", "source_id"),
    )
