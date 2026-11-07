"""Create configurable leave types, leave records, and a balance ledger."""
from alembic import op
import sqlalchemy as sa

revision = "0016_leave_management"
down_revision = "0015_attendance_issue_resolution"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "leave_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name_ar", sa.String(length=100), nullable=False),
        sa.Column("name_en", sa.String(length=100), nullable=False),
        sa.Column("is_paid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("tracks_balance", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("code", name="uq_leave_types_code"),
        sa.CheckConstraint("length(trim(code)) > 0", name="ck_leave_types_code_nonempty"),
    )
    op.create_table(
        "leave_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("leave_type_id", sa.Integer(), sa.ForeignKey("leave_types.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("calendar_days", sa.Integer(), nullable=False),
        sa.Column("charged_days", sa.Numeric(7, 2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_by", sa.String(length=80), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.String(length=80), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("end_date >= start_date", name="ck_leave_records_date_range"),
        sa.CheckConstraint("calendar_days >= 1", name="ck_leave_records_calendar_days"),
        sa.CheckConstraint("charged_days >= 0", name="ck_leave_records_charged_days"),
        sa.CheckConstraint("status IN ('draft', 'confirmed', 'cancelled')", name="ck_leave_records_status"),
    )
    op.create_index("ix_leave_records_employee_id", "leave_records", ["employee_id"])
    op.create_index("ix_leave_records_leave_type_id", "leave_records", ["leave_type_id"])
    op.create_index("ix_leave_records_start_date", "leave_records", ["start_date"])
    op.create_index("ix_leave_records_end_date", "leave_records", ["end_date"])

    op.create_table(
        "leave_balance_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("leave_type_id", sa.Integer(), sa.ForeignKey("leave_types.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("leave_record_id", sa.Integer(), sa.ForeignKey("leave_records.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("transaction_type", sa.String(length=24), nullable=False),
        sa.Column("days_delta", sa.Numeric(7, 2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "transaction_type IN ('opening', 'adjustment', 'leave_use', 'leave_reversal')",
            name="ck_leave_balance_transaction_type",
        ),
        sa.CheckConstraint("days_delta <> 0", name="ck_leave_balance_transaction_nonzero"),
        sa.UniqueConstraint(
            "leave_record_id", "transaction_type",
            name="uq_leave_balance_transaction_record_type",
        ),
    )
    op.create_index("ix_leave_balance_transactions_employee_id", "leave_balance_transactions", ["employee_id"])
    op.create_index("ix_leave_balance_transactions_leave_type_id", "leave_balance_transactions", ["leave_type_id"])
    op.create_index("ix_leave_balance_transactions_leave_record_id", "leave_balance_transactions", ["leave_record_id"])

    # Seed leave categories only; no employee entitlement or opening balance is invented.
    leave_types = sa.table(
        "leave_types",
        sa.column("code", sa.String),
        sa.column("name_ar", sa.String),
        sa.column("name_en", sa.String),
        sa.column("is_paid", sa.Boolean),
        sa.column("tracks_balance", sa.Boolean),
        sa.column("active", sa.Boolean),
    )
    op.bulk_insert(leave_types, [
        {"code": "annual", "name_ar": "سنوية", "name_en": "Annual", "is_paid": True, "tracks_balance": True, "active": True},
        {"code": "sick", "name_ar": "مرضية", "name_en": "Sick", "is_paid": True, "tracks_balance": True, "active": True},
        {"code": "casual", "name_ar": "عارضة / طارئة", "name_en": "Casual / Emergency", "is_paid": True, "tracks_balance": True, "active": True},
        {"code": "official_holiday", "name_ar": "عطلة رسمية", "name_en": "Official Holiday", "is_paid": True, "tracks_balance": False, "active": True},
        {"code": "weekly_rest", "name_ar": "راحة أسبوعية", "name_en": "Weekly Rest", "is_paid": True, "tracks_balance": False, "active": True},
        {"code": "unpaid", "name_ar": "بدون أجر", "name_en": "Unpaid", "is_paid": False, "tracks_balance": False, "active": True},
    ])


def downgrade():
    # Leave history is business data. Do not erase it during a version rollback.
    raise RuntimeError("Migration 0016 is forward-only; preserve leave records and balance history.")
