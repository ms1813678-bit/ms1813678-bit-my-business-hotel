"""Add monthly attendance lifecycle and approval metadata."""
from alembic import op
import sqlalchemy as sa


revision = "0013_attendance_months"
down_revision = "0012_attendance_approval_values"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "attendance_months",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column(
            "program_import_batch_id", sa.Integer(),
            sa.ForeignKey("attendance_import_batches.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "fingerprint_import_batch_id", sa.Integer(),
            sa.ForeignKey("attendance_import_batches.id", ondelete="RESTRICT"),
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="imported"),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", sa.String(80)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by", sa.String(80)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approval_reason", sa.Text()),
        sa.CheckConstraint("period_end >= period_start", name="ck_attendance_month_period"),
        sa.CheckConstraint(
            "status IN ('imported', 'under_review', 'approved')",
            name="ck_attendance_month_status",
        ),
        sa.UniqueConstraint("period_start", "period_end", name="uq_attendance_month_period"),
    )


def downgrade():
    op.drop_table("attendance_months")
