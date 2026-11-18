"""Add separately approved clock-in and clock-out values to daily attendance."""
from alembic import op
import sqlalchemy as sa


revision = "0012_attendance_approval_values"
down_revision = "0011_attendance_import_issues"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("attendance_daily_records")}
    if "approved_clock_in" not in columns:
        op.add_column(
            "attendance_daily_records",
            sa.Column("approved_clock_in", sa.DateTime(timezone=False), nullable=True),
        )
    if "approved_clock_out" not in columns:
        op.add_column(
            "attendance_daily_records",
            sa.Column("approved_clock_out", sa.DateTime(timezone=False), nullable=True),
        )


def downgrade():
    op.drop_column("attendance_daily_records", "approved_clock_out")
    op.drop_column("attendance_daily_records", "approved_clock_in")
