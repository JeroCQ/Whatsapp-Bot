"""Resumable bounded synchronization and worker requests, CRM database only."""

import sqlalchemy as sa

from alembic import op

revision = "0002_sync_controls"
down_revision = "0001_crm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_conversations", sa.Column("dirty", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.create_index("ix_source_conversations_dirty", "source_conversations", ["dirty"])
    op.add_column(
        "sync_status", sa.Column("run_state", sa.String(30), nullable=False, server_default="pending")
    )
    op.add_column("sync_status", sa.Column("failures", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("sync_status", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sync_status", sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"))
    for table, column in (
        ("source_conversations", "dirty"),
        ("sync_status", "run_state"),
        ("sync_status", "failures"),
        ("sync_status", "metrics"),
    ):
        op.alter_column(table, column, server_default=None)
    op.create_table(
        "sync_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("business_id", sa.String(63), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
    )
    op.create_index("ix_sync_requests_business_id", "sync_requests", ["business_id"])
    op.create_index("ix_sync_requests_status", "sync_requests", ["status"])
    op.create_index("uq_sync_request_key", "sync_requests", ["business_id", "idempotency_key"], unique=True)


def downgrade() -> None:
    op.drop_table("sync_requests")
    for column in ("metrics", "next_retry_at", "failures", "run_state"):
        op.drop_column("sync_status", column)
    op.drop_index("ix_source_conversations_dirty", table_name="source_conversations")
    op.drop_column("source_conversations", "dirty")
