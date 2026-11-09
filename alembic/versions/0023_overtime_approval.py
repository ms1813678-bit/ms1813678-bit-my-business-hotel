"""Persist explicit HR overtime approval metadata.

Positive overtime must never enter payroll calculations without an auditable approval.
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_overtime_approval"
down_revision = "0022_advance_type"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("payroll_items")}
    if "overtime_approved" not in columns:
        op.add_column(
            "payroll_items",
            sa.Column("overtime_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "overtime_approved_by" not in columns:
        op.add_column(
            "payroll_items",
            sa.Column("overtime_approved_by", sa.String(80), nullable=True),
        )
    if "overtime_approved_at" not in columns:
        op.add_column(
            "payroll_items",
            sa.Column("overtime_approved_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade():
    # Preserve payroll audit history; do not remove approval evidence.
    pass
