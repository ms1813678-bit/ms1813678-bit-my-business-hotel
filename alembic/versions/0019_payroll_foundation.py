"""Add monthly payroll periods, employee snapshots and payment records."""

from alembic import op
import sqlalchemy as sa


revision = "0019_payroll_foundation"
down_revision = "0018_staff_actions"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "payroll_periods" not in tables:
        op.create_table(
            "payroll_periods",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("period_start", sa.Date(), nullable=False),
            sa.Column("period_end", sa.Date(), nullable=False),
            sa.Column("payment_due_date", sa.Date(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("created_by", sa.String(length=80), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("approved_by", sa.String(length=80), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("closed_by", sa.String(length=80), nullable=True),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reopened_by", sa.String(length=80), nullable=True),
            sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reopen_reason", sa.Text(), nullable=True),
            sa.UniqueConstraint("period_start", name="uq_payroll_period_start"),
            sa.CheckConstraint("period_end >= period_start", name="ck_payroll_period_dates"),
            sa.CheckConstraint("status IN ('draft', 'approved', 'closed')", name="ck_payroll_period_status"),
        )
        tables.add("payroll_periods")

    if "payroll_lines" not in tables:
        op.create_table(
            "payroll_lines",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("payroll_period_id", sa.Integer(), sa.ForeignKey("payroll_periods.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("employee_code_snapshot", sa.String(length=20), nullable=False),
            sa.Column("employee_name_snapshot", sa.String(length=200), nullable=False),
            sa.Column("department_name_snapshot", sa.String(length=100), nullable=False),
            sa.Column("position_snapshot", sa.String(length=120), nullable=False),
            sa.Column("basic_salary_snapshot", sa.Numeric(12, 2), nullable=False),
            sa.Column("monthly_basic_salary_snapshot", sa.Numeric(12, 2), nullable=False),
            sa.Column("payable_service_days", sa.Integer(), nullable=False),
            sa.Column("daily_hours_snapshot", sa.Numeric(5, 2), nullable=False),
            sa.Column("attendance_bonus", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("performance_bonus", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("occupancy_bonus", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("overtime_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("other_bonus", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("absence_deduction", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("lateness_deduction", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("insurance_share", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("taxes", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("other_deductions", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("advance_deduction", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.UniqueConstraint("payroll_period_id", "employee_id", name="uq_payroll_line_period_employee"),
            sa.CheckConstraint("basic_salary_snapshot >= 0 AND monthly_basic_salary_snapshot >= 0", name="ck_payroll_line_base_salary_nonnegative"),
            sa.CheckConstraint("payable_service_days BETWEEN 0 AND 31", name="ck_payroll_line_service_days"),
            sa.CheckConstraint(
                "attendance_bonus >= 0 AND performance_bonus >= 0 AND occupancy_bonus >= 0 "
                "AND overtime_amount >= 0 AND other_bonus >= 0 "
                "AND absence_deduction >= 0 AND lateness_deduction >= 0 "
                "AND insurance_share >= 0 AND taxes >= 0 AND other_deductions >= 0 "
                "AND advance_deduction >= 0",
                name="ck_payroll_line_amounts_nonnegative",
            ),
        )
        tables.add("payroll_lines")

    if "payroll_payments" not in tables:
        op.create_table(
            "payroll_payments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("payroll_line_id", sa.Integer(), sa.ForeignKey("payroll_lines.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("payment_date", sa.Date(), nullable=False),
            sa.Column("payment_method", sa.String(length=20), nullable=False),
            sa.Column("reference", sa.String(length=120), nullable=True),
            sa.Column("recorded_by", sa.String(length=80), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("amount > 0", name="ck_payroll_payment_amount_positive"),
            sa.CheckConstraint("payment_method IN ('cash', 'bank_transfer')", name="ck_payroll_payment_method"),
        )

    inspector = sa.inspect(bind)
    for table, index, columns in (
        ("payroll_lines", "ix_payroll_lines_payroll_period_id", ["payroll_period_id"]),
        ("payroll_lines", "ix_payroll_lines_employee_id", ["employee_id"]),
        ("payroll_payments", "ix_payroll_payments_payroll_line_id", ["payroll_line_id"]),
    ):
        existing = {item["name"] for item in inspector.get_indexes(table)}
        if index not in existing:
            op.create_index(index, table, columns)


def downgrade():
    # Payroll history is financial evidence; preserve it during schema rollback.
    pass
