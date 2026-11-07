"""Create payroll periods, employee payroll items, and payment history."""
from alembic import op
import sqlalchemy as sa


revision = "0019_payroll_runs"
down_revision = "0018_staff_actions"
branch_labels = None
depends_on = None


def upgrade():
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "payroll_runs" not in existing_tables:
        op.create_table(
            "payroll_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("period_start", sa.Date(), nullable=False),
            sa.Column("period_end", sa.Date(), nullable=False),
            sa.Column("pay_date", sa.Date(), nullable=False),
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
            sa.UniqueConstraint("period_start", name="uq_payroll_runs_period_start"),
            sa.CheckConstraint("period_end >= period_start", name="ck_payroll_runs_period"),
            sa.CheckConstraint(
                "status IN ('draft', 'approved', 'closed', 'reopened')",
                name="ck_payroll_runs_status",
            ),
        )
        op.create_index("ix_payroll_runs_period_start", "payroll_runs", ["period_start"])

    if "payroll_items" not in existing_tables:
        op.create_table(
            "payroll_items",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("payroll_run_id", sa.Integer(), sa.ForeignKey("payroll_runs.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("salary_basis", sa.Numeric(12, 2), nullable=False),
            sa.Column("base_salary", sa.Numeric(12, 2), nullable=False),
            sa.Column("daily_hours", sa.Numeric(6, 2), nullable=False),
            sa.Column("attendance_bonus", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("performance_bonus", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("occupancy_bonus", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("overtime_hours", sa.Numeric(8, 2), nullable=False, server_default="0"),
            sa.Column("overtime_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("other_bonus", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("absence_days", sa.Numeric(6, 2), nullable=False, server_default="0"),
            sa.Column("absence_deduction", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("late_minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("early_minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lateness_deduction", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("insurance_share", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("taxes", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("other_deductions", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("advance_deduction", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("staff_reward_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("staff_sanction_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("net_pay", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("payroll_run_id", "employee_id", name="uq_payroll_items_run_employee"),
            sa.CheckConstraint("salary_basis >= 0", name="ck_payroll_items_salary_basis"),
            sa.CheckConstraint("base_salary >= 0", name="ck_payroll_items_base_salary"),
            sa.CheckConstraint("daily_hours > 0", name="ck_payroll_items_daily_hours"),
            sa.CheckConstraint("overtime_hours >= 0", name="ck_payroll_items_overtime_hours"),
            sa.CheckConstraint("absence_days >= 0", name="ck_payroll_items_absence_days"),
            sa.CheckConstraint("late_minutes >= 0 AND early_minutes >= 0", name="ck_payroll_items_minutes"),
            sa.CheckConstraint(
                "attendance_bonus >= 0 AND performance_bonus >= 0 AND occupancy_bonus >= 0 "
                "AND overtime_amount >= 0 AND other_bonus >= 0 AND absence_deduction >= 0 "
                "AND lateness_deduction >= 0 AND insurance_share >= 0 AND taxes >= 0 "
                "AND other_deductions >= 0 AND advance_deduction >= 0 "
                "AND staff_reward_amount >= 0 AND staff_sanction_amount >= 0 AND net_pay >= 0",
                name="ck_payroll_items_nonnegative_amounts",
            ),
        )
        op.create_index("ix_payroll_items_payroll_run_id", "payroll_items", ["payroll_run_id"])
        op.create_index("ix_payroll_items_employee_id", "payroll_items", ["employee_id"])

    if "payroll_payments" not in existing_tables:
        op.create_table(
            "payroll_payments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("payroll_item_id", sa.Integer(), sa.ForeignKey("payroll_items.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("method", sa.String(length=20), nullable=False),
            sa.Column("reference", sa.String(length=200), nullable=True),
            sa.Column("created_by", sa.String(length=80), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("amount > 0", name="ck_payroll_payments_amount_positive"),
            sa.CheckConstraint("method IN ('cash', 'bank_transfer')", name="ck_payroll_payments_method"),
        )
        op.create_index("ix_payroll_payments_payroll_item_id", "payroll_payments", ["payroll_item_id"])

    # Link financial source records to the payroll run that consumed them.
    for table, column in (
        ("advance_requests", "deducted_payroll_run_id"),
        ("staff_actions", "payroll_run_id"),
    ):
        columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}
        if column not in columns:
            op.add_column(
                table,
                sa.Column(column, sa.Integer(), sa.ForeignKey("payroll_runs.id", ondelete="RESTRICT"), nullable=True),
            )
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    # Payroll and payment records are financial history; never erase them on downgrade.
    pass
