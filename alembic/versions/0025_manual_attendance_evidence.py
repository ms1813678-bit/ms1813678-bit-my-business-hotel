"""Add administrator-entered attendance evidence without replacing source imports.

Revision ID: 0025_manual_attendance_evidence
Revises: 0024_merge_advance_and_ai_heads
Create Date: 2026-11-19
"""
from alembic import op
import sqlalchemy as sa


revision = "0025_manual_attendance_evidence"
down_revision = "0024_merge_advance_and_ai_heads"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("attendance_daily_records")}
    additions = (
        ("manual_clock_in", sa.DateTime(timezone=False)),
        ("manual_clock_out", sa.DateTime(timezone=False)),
        ("manual_entry_reason", sa.Text()),
        ("manual_entered_by", sa.String(80)),
        ("manual_entered_at", sa.DateTime(timezone=True)),
    )
    for name, column_type in additions:
        if name not in columns:
            op.add_column(
                "attendance_daily_records",
                sa.Column(name, column_type, nullable=True),
            )


def downgrade():
    # Preserve historical manual attendance evidence.
    pass
