"""Retain original attendance source workbooks for audit and re-download."""
from alembic import op
import sqlalchemy as sa


revision = "0014_attendance_source_files"
down_revision = "0013_attendance_months"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "attendance_import_batches",
        sa.Column("original_file_bytes", sa.LargeBinary(), nullable=True),
    )


def downgrade():
    op.drop_column("attendance_import_batches", "original_file_bytes")
