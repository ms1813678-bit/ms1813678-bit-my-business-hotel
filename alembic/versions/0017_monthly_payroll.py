"""Create monthly payroll periods, employee snapshots, and payments."""
from alembic import op
import sqlalchemy as sa

revision = "0017_monthly_payroll"
down_revision = "0016_leave_management"
branch_labels = None
depends_on = None


def upgrade():
    required = {"payroll_periods", "payroll_lines", "payroll_payments"}
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if required.issubset(existing):
        return
    if required.intersection(existing):
        raise RuntimeError("Partial payroll schema detected; refusing an unsafe upgrade.")
    op.create_table(
        "payroll_periods",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("attendance_month_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.String(80)), sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approval_reason", sa.Text()), sa.Column("closed_by", sa.String(80)),
        sa.Column("closed_at", sa.DateTime(timezone=True)), sa.Column("reopened_by", sa.String(80)),
        sa.Column("reopened_at", sa.DateTime(timezone=True)), sa.Column("reopen_reason", sa.Text()),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="ck_payroll_period_month"),
        sa.CheckConstraint("year >= 2000", name="ck_payroll_period_year"),
        sa.CheckConstraint("status IN ('draft', 'approved', 'closed', 'reopened')", name="ck_payroll_period_status"),
        sa.ForeignKeyConstraint(["attendance_month_id"], ["attendance_months.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("year", "month", name="uq_payroll_period_year_month"),
        sa.UniqueConstraint("attendance_month_id"),
    )
    op.create_table(
        "payroll_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payroll_period_id", sa.Integer(), nullable=False), sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("employee_code", sa.String(20), nullable=False), sa.Column("employee_name", sa.String(200), nullable=False),
        sa.Column("base_salary", sa.Numeric(12, 2), nullable=False), sa.Column("daily_hours", sa.Numeric(5, 2), nullable=False),
        *[sa.Column(n, sa.Numeric(12, 2), nullable=False, server_default="0") for n in
          ("attendance_bonus","performance_bonus","occupancy_bonus","other_bonuses","insurance_share","tax_withheld","other_deductions","advance_deduction")],
        sa.Column("overtime_hours", sa.Numeric(8, 2), nullable=False, server_default="0"),
        sa.Column("absence_days", sa.Numeric(6, 2), nullable=False, server_default="0"),
        sa.Column("late_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("early_minutes", sa.Integer(), nullable=False, server_default="0"),
        *[sa.Column(n, sa.Numeric(12, 2), nullable=False) for n in
          ("base_pay","overtime_pay","absence_deduction","lateness_deduction","gross_pay","net_pay")],
        sa.Column("payment_status", sa.String(20), nullable=False, server_default="unpaid"),
        sa.Column("review_notes", sa.Text()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("base_salary >= 0", name="ck_payroll_line_salary_nonnegative"),
        sa.CheckConstraint("daily_hours > 0 AND daily_hours <= 24", name="ck_payroll_line_hours_range"),
        sa.CheckConstraint("absence_days >= 0", name="ck_payroll_line_absence_nonnegative"),
        sa.CheckConstraint("late_minutes >= 0 AND early_minutes >= 0", name="ck_payroll_line_minutes_nonnegative"),
        sa.CheckConstraint("attendance_bonus >= 0 AND performance_bonus >= 0 AND occupancy_bonus >= 0 AND overtime_hours >= 0 AND other_bonuses >= 0", name="ck_payroll_line_earnings_nonnegative"),
        sa.CheckConstraint("insurance_share >= 0 AND tax_withheld >= 0 AND other_deductions >= 0 AND advance_deduction >= 0", name="ck_payroll_line_deductions_nonnegative"),
        sa.ForeignKeyConstraint(["payroll_period_id"], ["payroll_periods.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("payroll_period_id", "employee_id", name="uq_payroll_period_employee"),
    )
    op.create_index("ix_payroll_lines_payroll_period_id", "payroll_lines", ["payroll_period_id"])
    op.create_index("ix_payroll_lines_employee_id", "payroll_lines", ["employee_id"])
    op.create_table(
        "payroll_payments",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("payroll_line_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False), sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("method", sa.String(30), nullable=False), sa.Column("reference", sa.String(120)),
        sa.Column("created_by", sa.String(80), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_payroll_payment_amount_positive"),
        sa.CheckConstraint("method IN ('cash', 'bank_transfer')", name="ck_payroll_payment_method"),
        sa.ForeignKeyConstraint(["payroll_line_id"], ["payroll_lines.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_payroll_payments_payroll_line_id", "payroll_payments", ["payroll_line_id"])


def downgrade():
    op.drop_index("ix_payroll_payments_payroll_line_id", table_name="payroll_payments")
    op.drop_table("payroll_payments")
    op.drop_index("ix_payroll_lines_employee_id", table_name="payroll_lines")
    op.drop_index("ix_payroll_lines_payroll_period_id", table_name="payroll_lines")
    op.drop_table("payroll_lines")
    op.drop_table("payroll_periods")
