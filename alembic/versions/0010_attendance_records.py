"""Create raw attendance import and daily reconciliation tables."""
from alembic import op
import sqlalchemy as sa


revision = "0010_attendance_records"
down_revision = "0009_audit_log_immutability"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "attendance_import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("uploaded_by", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="uploaded"),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("issue_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reviewed_by", sa.String(80)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source_type IN ('program_report', 'fingerprint')", name="ck_attendance_import_source_type"),
        sa.CheckConstraint("status IN ('uploaded', 'parsed', 'needs_review', 'approved', 'rejected')", name="ck_attendance_import_status"),
        sa.CheckConstraint("period_end >= period_start", name="ck_attendance_import_period"),
        sa.CheckConstraint("row_count >= 0 AND issue_count >= 0", name="ck_attendance_import_counts"),
        sa.UniqueConstraint("source_type", "file_sha256", name="uq_attendance_import_source_hash"),
    )
    op.create_index("ix_attendance_import_batches_source_type", "attendance_import_batches", ["source_type"])
    op.create_index("ix_attendance_import_batches_period_start", "attendance_import_batches", ["period_start"])
    op.create_index("ix_attendance_import_batches_period_end", "attendance_import_batches", ["period_end"])

    op.create_table(
        "attendance_punches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("import_batch_id", sa.Integer(), sa.ForeignKey("attendance_import_batches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id", ondelete="RESTRICT")),
        sa.Column("raw_employee_code", sa.String(80)),
        sa.Column("raw_employee_name", sa.String(200)),
        sa.Column("punch_at", sa.DateTime(timezone=False)),
        sa.Column("raw_timestamp", sa.String(120), nullable=False),
        sa.Column("event_type", sa.String(40)),
        sa.Column("source_sheet", sa.String(120), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("event_index", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("event_index >= 0", name="ck_attendance_punch_event_index"),
        sa.UniqueConstraint("import_batch_id", "source_sheet", "source_row", "event_index", name="uq_attendance_punch_source_position"),
    )
    op.create_index("ix_attendance_punches_import_batch_id", "attendance_punches", ["import_batch_id"])
    op.create_index("ix_attendance_punches_employee_id", "attendance_punches", ["employee_id"])
    op.create_index("ix_attendance_punches_punch_at", "attendance_punches", ["punch_at"])

    op.create_table(
        "attendance_daily_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("shift_pattern_id", sa.Integer(), sa.ForeignKey("shift_patterns.id")),
        sa.Column("security_shift_assignment_id", sa.Integer(), sa.ForeignKey("security_shift_assignments.id", ondelete="RESTRICT")),
        sa.Column("program_import_batch_id", sa.Integer(), sa.ForeignKey("attendance_import_batches.id", ondelete="RESTRICT")),
        sa.Column("fingerprint_import_batch_id", sa.Integer(), sa.ForeignKey("attendance_import_batches.id", ondelete="RESTRICT")),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=False)),
        sa.Column("scheduled_end_at", sa.DateTime(timezone=False)),
        sa.Column("program_clock_in", sa.DateTime(timezone=False)),
        sa.Column("program_clock_out", sa.DateTime(timezone=False)),
        sa.Column("first_clock_in", sa.DateTime(timezone=False)),
        sa.Column("last_clock_out", sa.DateTime(timezone=False)),
        sa.Column("worked_minutes", sa.Integer()),
        sa.Column("status", sa.String(24), nullable=False, server_default="needs_review"),
        sa.Column("review_notes", sa.Text()),
        sa.Column("approved_by", sa.String(80)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('needs_review', 'ready_for_review', 'approved', 'rejected')", name="ck_attendance_daily_status"),
        sa.CheckConstraint("scheduled_end_at IS NULL OR scheduled_start_at IS NULL OR scheduled_end_at > scheduled_start_at", name="ck_attendance_daily_schedule_order"),
        sa.CheckConstraint("worked_minutes IS NULL OR worked_minutes >= 0", name="ck_attendance_daily_worked_minutes"),
        sa.UniqueConstraint("employee_id", "work_date", name="uq_attendance_daily_employee_date"),
    )
    op.create_index("ix_attendance_daily_records_employee_id", "attendance_daily_records", ["employee_id"])
    op.create_index("ix_attendance_daily_records_work_date", "attendance_daily_records", ["work_date"])


def downgrade():
    op.drop_index("ix_attendance_daily_records_work_date", table_name="attendance_daily_records")
    op.drop_index("ix_attendance_daily_records_employee_id", table_name="attendance_daily_records")
    op.drop_table("attendance_daily_records")
    op.drop_index("ix_attendance_punches_punch_at", table_name="attendance_punches")
    op.drop_index("ix_attendance_punches_employee_id", table_name="attendance_punches")
    op.drop_index("ix_attendance_punches_import_batch_id", table_name="attendance_punches")
    op.drop_table("attendance_punches")
    op.drop_index("ix_attendance_import_batches_period_end", table_name="attendance_import_batches")
    op.drop_index("ix_attendance_import_batches_period_start", table_name="attendance_import_batches")
    op.drop_index("ix_attendance_import_batches_source_type", table_name="attendance_import_batches")
    op.drop_table("attendance_import_batches")
