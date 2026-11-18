"""Draft-only central configuration and usage/support storage. No runtime seeding."""
import sqlalchemy as sa

from alembic import op

revision = "0005_central_config_usage"
down_revision = "0004_delivery_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crm_business_drafts",
        sa.Column("business_id", sa.String(63), primary_key=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("whatsapp_phone_number_id", sa.String(32), nullable=True),
        sa.Column("whatsapp_business_account_id", sa.String(32), nullable=True),
        sa.Column("overrides", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(200), sa.ForeignKey("crm_agents.profile"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state = 'draft'", name="ck_business_draft_only"),
    )
    op.create_index("uq_draft_whatsapp_phone", "crm_business_drafts", ["whatsapp_phone_number_id"], unique=True)
    op.create_table(
        "crm_shared_config_draft",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(200), sa.ForeignKey("crm_agents.profile"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 'shared'", name="ck_single_shared_draft"),
    )
    op.create_table(
        "crm_business_connection_refs",
        sa.Column("business_id", sa.String(63), sa.ForeignKey("crm_business_drafts.business_id"), primary_key=True),
        sa.Column("purpose", sa.String(32), primary_key=True),
        sa.Column("reference_key", sa.String(200), nullable=False),
    )
    op.create_table(
        "crm_usage_turns",
        sa.Column("business_id", sa.String(63), primary_key=True),
        sa.Column("turn_id", sa.String(160), primary_key=True),
        sa.Column("conversation_id", sa.String(200), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("outcome IN ('unknown','reply_accepted','no_reply','failed')", name="ck_usage_turn_outcome"),
    )
    op.create_index("ix_usage_turn_conversation", "crm_usage_turns", ["business_id", "conversation_id"])
    op.create_table(
        "crm_model_usage_attempts",
        sa.Column("business_id", sa.String(63), primary_key=True),
        sa.Column("attempt_id", sa.String(160), primary_key=True),
        sa.Column("turn_id", sa.String(160), nullable=False),
        sa.Column("attempt_index", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(80), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("model_version", sa.String(200), nullable=True),
        *[sa.Column(name, sa.BigInteger(), nullable=True) for name in (
            "input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "thought_tokens", "latency_ms",
        )],
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("error_type", sa.String(80), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["business_id", "turn_id"], ["crm_usage_turns.business_id", "crm_usage_turns.turn_id"]),
        sa.CheckConstraint("attempt_index > 0", name="ck_usage_attempt_index"),
        sa.CheckConstraint("outcome IN ('success','failed','timeout','cancelled','unknown')", name="ck_usage_attempt_outcome"),
    )
    op.create_index("ix_crm_model_usage_attempts_observed_at", "crm_model_usage_attempts", ["observed_at"])
    op.create_index("ix_usage_attempt_business_time", "crm_model_usage_attempts", ["business_id", "observed_at", "attempt_id"])
    op.create_table(
        "crm_support_minutes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("business_id", sa.String(63), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("created_by", sa.String(200), sa.ForeignKey("crm_agents.profile"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("minutes > 0", name="ck_support_minutes_positive"),
    )
    op.create_index("ix_crm_support_minutes_business_id", "crm_support_minutes", ["business_id"])


def downgrade() -> None:
    # Explicit operator-only rollback; never run automatically (usage would be lost).
    for table in ("crm_support_minutes", "crm_model_usage_attempts", "crm_usage_turns",
                  "crm_business_connection_refs", "crm_shared_config_draft", "crm_business_drafts"):
        op.drop_table(table)
