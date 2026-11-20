"""Retain attendance import issues for explicit HR review."""
from alembic import op
import sqlalchemy as sa


revision = "0011_attendance_import_issues"
down_revision = "0010_attendance_records"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "attendance_import_issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("import_batch_id", sa.Integer(), sa.ForeignKey("attendance_import_batches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_sheet", sa.String(120)),
        sa.Column("source_row", sa.Integer()),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("raw_value", sa.Text()),
        sa.Column("details", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source_row IS NULL OR source_row >= 0", name="ck_attendance_import_issue_source_row"),
    )
    op.create_index("ix_attendance_import_issues_import_batch_id", "attendance_import_issues", ["import_batch_id"])


def downgrade():
    op.drop_index("ix_attendance_import_issues_import_batch_id", table_name="attendance_import_issues")
    op.drop_table("attendance_import_issues")
