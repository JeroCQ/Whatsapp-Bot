"""Store scoped, evidence-linked commercial events in the CRM database."""

from alembic import op
import sqlalchemy as sa

revision = "0008_commercial_events"
down_revision = "0007_customer_insights"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "crm_commercial_events",
        sa.Column("business_id", sa.String(63), primary_key=True),
        sa.Column("source_id", sa.BigInteger(), primary_key=True),
        sa.Column("kind", sa.String(20), primary_key=True),
        sa.Column("phone_number", sa.String(200), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("queue", sa.String(12), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount_cop", sa.BigInteger(), nullable=True),
        sa.Column("product_subtotal_cop", sa.BigInteger(), nullable=True),
        sa.Column("items", sa.JSON(), nullable=False),
    )
    op.create_index("ix_commercial_event_period", "crm_commercial_events", ["business_id", "occurred_at", "source_id"])
    op.create_index("ix_commercial_event_contact", "crm_commercial_events", ["business_id", "phone_number", "source_id"])
    op.create_index("ix_commercial_event_order", "crm_commercial_events", ["business_id", "order_id", "source_id"])


def downgrade():
    raise RuntimeError("Keep commercial and permission evidence. Roll back compatible code instead.")
