"""Durable CRM command deduplication. Bot remains operational authority."""

import sqlalchemy as sa

from alembic import op

revision = "0003_bot_commands"
down_revision = "0002_sync_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_commands",
        sa.Column("business_id", sa.String(63), primary_key=True),
        sa.Column("command_id", sa.String(100), primary_key=True),
        sa.Column("phone_number", sa.String(200), nullable=False),
        sa.Column("profile", sa.String(200), nullable=False),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bot_commands_phone_number", "bot_commands", ["phone_number"])


def downgrade() -> None:
    op.drop_table("bot_commands")
