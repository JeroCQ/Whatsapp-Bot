"""Private CRM promotion evidence and confirmed-return analysis jobs."""

from alembic import op
import sqlalchemy as sa

revision = "0007_customer_insights"
down_revision = "0006_scoped_pilot"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "promotion_permissions",
        sa.Column("business_id", sa.String(63), primary_key=True),
        sa.Column("sender_id", sa.String(63), primary_key=True),
        sa.Column("phone_number", sa.String(200), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True)),
        sa.Column("event_id", sa.String(300)),
        sa.CheckConstraint("status IN ('unknown','granted','denied','revoked')", name="ck_promotion_permission_status"),
    )
    op.create_index("ix_promotion_permission_audience", "promotion_permissions",
                    ["business_id", "sender_id", "status", "phone_number"])
    op.create_table(
        "promotion_permission_events",
        sa.Column("business_id", sa.String(63), primary_key=True),
        sa.Column("sender_id", sa.String(63), primary_key=True),
        sa.Column("inbound_id", sa.String(300), primary_key=True),
        sa.Column("phone_number", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("question_id", sa.String(300)),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", sa.String(160), nullable=False),
        sa.Column("validated", sa.Boolean(), nullable=False),
        sa.CheckConstraint("status IN ('granted','denied','revoked')", name="ck_promotion_permission_event_status"),
    )
    op.create_index("ix_promotion_permission_questions", "promotion_permission_events",
                    ["business_id", "phone_number", "question_id"], postgresql_where=sa.text("NOT validated"))
    op.create_index("ix_promotion_permission_contact_history", "promotion_permission_events",
                    ["business_id", "sender_id", "phone_number", "event_at"])
    op.create_table(
        "customer_return_analyses",
        sa.Column("business_id", sa.String(63), primary_key=True),
        sa.Column("command_id", sa.String(160), primary_key=True),
        sa.Column("phone_number", sa.String(200), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.CheckConstraint("status IN ('pending','processing','done','failed')", name="ck_customer_analysis_status"),
    )
    op.create_index("ix_customer_analysis_queue", "customer_return_analyses", ["status", "retry_at", "released_at"])
    op.create_index("ix_customer_analysis_history", "customer_return_analyses", ["business_id", "phone_number", "released_at"])


def downgrade():
    op.drop_table("customer_return_analyses")
    op.drop_table("promotion_permission_events")
    op.drop_table("promotion_permissions")
