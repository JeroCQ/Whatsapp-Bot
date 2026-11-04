"""Project incremental delivery facts without rewriting source audit events."""

import sqlalchemy as sa

from alembic import op

revision = "0004_delivery_receipts"
down_revision = "0003_bot_commands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE crm_delivery_revision_seq")
    op.add_column("source_messages", sa.Column("delivery_status", sa.String(20), nullable=True))
    op.add_column("source_messages", sa.Column("command_id", sa.String(160), nullable=True))
    op.add_column("source_messages", sa.Column("provider_message_id", sa.String(300), nullable=True))
    op.add_column(
        "source_messages",
        sa.Column("delivery_revision", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "source_messages",
        sa.Column("is_delivery_event", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    for name, suffix in (
        ("ix_source_command", "command_id"),
        ("ix_source_provider", "provider_message_id"),
        ("ix_source_delivery_revision", "delivery_revision"),
    ):
        op.create_index(name, "source_messages", ["business_id", "phone_number", suffix])
    op.create_table(
        "delivery_receipts",
        sa.Column("business_id", sa.String(63), primary_key=True),
        sa.Column("event_source_id", sa.BigInteger(), primary_key=True),
        sa.Column("phone_number", sa.String(200), nullable=False),
        sa.Column("command_id", sa.String(160), nullable=True),
        sa.Column("provider_message_id", sa.String(300), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("matched_source_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["business_id", "event_source_id"], ["source_messages.business_id", "source_messages.source_id"]
        ),
        sa.ForeignKeyConstraint(
            ["business_id", "matched_source_id"], ["source_messages.business_id", "source_messages.source_id"]
        ),
    )
    op.create_index("ix_receipt_command", "delivery_receipts", ["business_id", "phone_number", "command_id"])
    op.create_index(
        "ix_receipt_provider", "delivery_receipts", ["business_id", "phone_number", "provider_message_id"]
    )
    # Existing CRM databases may already contain technical receipts below their
    # sync watermark. Backfill them once here, without a source history rescan.
    op.execute("""
        UPDATE source_messages
        SET command_id = NULLIF(COALESCE(metadata->>'command_id', metadata->>'source_event_id'), ''),
            provider_message_id = NULLIF(metadata->>'provider_message_id', ''),
            delivery_status = CASE WHEN metadata->>'delivery_status' = 'sent' THEN 'accepted'
                WHEN metadata->>'delivery_status' IN ('accepted','delivered','read','failed','unknown')
                THEN metadata->>'delivery_status' ELSE NULL END,
            delivery_revision = nextval('crm_delivery_revision_seq')
        WHERE role IN ('asesor','model')
          AND length(COALESCE(metadata->>'command_id', metadata->>'source_event_id','')) <= 160
          AND length(COALESCE(metadata->>'provider_message_id','')) <= 300
    """)
    op.execute("""
        UPDATE source_messages SET is_delivery_event = true
        WHERE role='system' AND metadata->>'source'='crm_delivery'
          AND content LIKE 'DELIVERY_STATUS:%'
    """)
    op.execute("""
        INSERT INTO delivery_receipts
            (business_id,event_source_id,phone_number,command_id,provider_message_id,status,received_at)
        SELECT business_id,source_id,phone_number,
            NULLIF(COALESCE(metadata->>'command_id',metadata->>'source_event_id'),''),
            NULLIF(metadata->>'provider_message_id',''),
            CASE WHEN metadata->>'delivery_status'='sent' THEN 'accepted' ELSE metadata->>'delivery_status' END,
            created_at
        FROM source_messages
        WHERE is_delivery_event
          AND metadata->>'delivery_status' IN ('accepted','delivered','read','failed','unknown','sent')
          AND length(COALESCE(metadata->>'command_id',metadata->>'source_event_id','')) <= 160
          AND length(COALESCE(metadata->>'provider_message_id','')) <= 300
          AND (NULLIF(COALESCE(metadata->>'command_id',metadata->>'source_event_id'),'') IS NOT NULL
               OR NULLIF(metadata->>'provider_message_id','') IS NOT NULL)
    """)
    op.execute("""
        WITH candidates AS (
            SELECT r.business_id,r.event_source_id,min(m.source_id) AS target_id
            FROM delivery_receipts r JOIN source_messages m
              ON m.business_id=r.business_id AND m.phone_number=r.phone_number
              AND m.role IN ('asesor','model') AND NOT m.is_delivery_event
              AND ((r.command_id IS NOT NULL AND r.command_id=m.command_id)
                   OR (r.provider_message_id IS NOT NULL AND r.provider_message_id=m.provider_message_id))
              AND (r.command_id IS NULL OR m.command_id IS NULL OR r.command_id=m.command_id)
              AND (r.provider_message_id IS NULL OR m.provider_message_id IS NULL
                   OR r.provider_message_id=m.provider_message_id)
            GROUP BY r.business_id,r.event_source_id HAVING count(*)=1
        ) UPDATE delivery_receipts r SET matched_source_id=c.target_id
          FROM candidates c WHERE r.business_id=c.business_id AND r.event_source_id=c.event_source_id
    """)
    op.execute("""
        WITH evidence AS (
            SELECT business_id,source_id,delivery_status AS status FROM source_messages
            WHERE role IN ('asesor','model')
            UNION ALL
            SELECT business_id,matched_source_id,status FROM delivery_receipts WHERE matched_source_id IS NOT NULL
        ), best AS (
            SELECT business_id,source_id,max(CASE status WHEN 'read' THEN 4 WHEN 'delivered' THEN 3
              WHEN 'failed' THEN 2 WHEN 'accepted' THEN 1 WHEN 'unknown' THEN 0 ELSE -1 END) AS rank
            FROM evidence GROUP BY business_id,source_id
        ) UPDATE source_messages m SET delivery_status=CASE best.rank WHEN 4 THEN 'read' WHEN 3 THEN 'delivered'
              WHEN 2 THEN 'failed' WHEN 1 THEN 'accepted' WHEN 0 THEN 'unknown' ELSE NULL END,
              delivery_revision=nextval('crm_delivery_revision_seq')
          FROM best WHERE m.business_id=best.business_id AND m.source_id=best.source_id
    """)


def downgrade() -> None:
    op.drop_table("delivery_receipts")
    for name in ("ix_source_delivery_revision", "ix_source_provider", "ix_source_command"):
        op.drop_index(name, table_name="source_messages")
    for column in (
        "is_delivery_event",
        "delivery_revision",
        "provider_message_id",
        "command_id",
        "delivery_status",
    ):
        op.drop_column("source_messages", column)
    op.execute("DROP SEQUENCE crm_delivery_revision_seq")
