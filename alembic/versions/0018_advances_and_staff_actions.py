"""Salary advances and staff sanctions/rewards tables."""
from alembic import op
import sqlalchemy as sa

revision = "0018_advances_and_staff_actions"
down_revision = "0017_monthly_payroll"
branch_labels = None
depends_on = None


def upgrade():
    required = {"salary_advances", "staff_actions"}
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if required.issubset(existing):
        return
    if required.intersection(existing):
        raise RuntimeError("Partial advances/staff-actions schema detected; refusing unsafe upgrade.")

    op.create_table(
        "salary_advances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("request_date", sa.Date(), nullable=False),
        sa.Column("request_day", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("payroll_year", sa.Integer(), nullable=False),
        sa.Column("payroll_month", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_by", sa.String(80)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_reason", sa.Text()),
        sa.Column("deducted_payroll_line_id", sa.Integer()),
        sa.CheckConstraint("amount > 0", name="ck_salary_advance_amount_positive"),
        sa.CheckConstraint("status IN ('active', 'deducted', 'cancelled')", name="ck_salary_advance_status"),
        sa.CheckConstraint("request_day BETWEEN 15 AND 22", name="ck_salary_advance_request_window_day"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["deducted_payroll_line_id"], ["payroll_lines.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_salary_advances_employee_id", "salary_advances", ["employee_id"])
    op.create_index("ix_salary_advances_request_date", "salary_advances", ["request_date"])

    op.create_table(
        "staff_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("action_kind", sa.String(20), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("action_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2)),
        sa.Column("payroll_year", sa.Integer()),
        sa.Column("payroll_month", sa.Integer()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_by", sa.String(80)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_reason", sa.Text()),
        sa.CheckConstraint("action_kind IN ('sanction', 'reward')", name="ck_staff_action_kind"),
        sa.CheckConstraint("status IN ('active', 'cancelled')", name="ck_staff_action_status"),
        sa.CheckConstraint("amount IS NULL OR amount >= 0", name="ck_staff_action_amount_nonnegative"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_staff_actions_employee_id", "staff_actions", ["employee_id"])
    op.create_index("ix_staff_actions_action_kind", "staff_actions", ["action_kind"])
    op.create_index("ix_staff_actions_action_date", "staff_actions", ["action_date"])


def downgrade():
    op.drop_table("staff_actions")
    op.drop_table("salary_advances")
