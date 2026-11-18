"""Versioned Briosos pilot. Isolated pilot CRM PostgreSQL ONLY; NOT APPLIED.

Revision ID: 0006_scoped_pilot
Revises: 0005_central_config_usage
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_scoped_pilot"
down_revision = "0005_central_config_usage"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("crm_business_drafts", sa.Column("operator_profiles", sa.JSON(), server_default="[]", nullable=False))
    op.create_table("crm_pilot_publications",
        sa.Column("version", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("business_id", sa.String(63), nullable=False),
        sa.Column("phone_number", sa.String(32), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(200), sa.ForeignKey("crm_agents.profile"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("business_id = 'briosos' AND phone_number = '573105338124'", name="ck_pilot_scope"))
    op.create_table("crm_pilot_switch",
        sa.Column("business_id", sa.String(63), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("publication_version", sa.Integer(), sa.ForeignKey("crm_pilot_publications.version")),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.CheckConstraint("business_id = 'briosos' AND mode IN ('standby','shared','legacy')", name="ck_pilot_switch"))


def downgrade():
    op.drop_table("crm_pilot_switch")
    op.drop_table("crm_pilot_publications")
    op.drop_column("crm_business_drafts", "operator_profiles")
