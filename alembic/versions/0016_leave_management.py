"""Create administrator-managed leave and auditable balance tables."""
from alembic import op
import sqlalchemy as sa


revision = "0016_leave_management"
down_revision = "0015_attendance_issue_resolution"
branch_labels = None
depends_on = None


def upgrade():
    expected_tables = {
        "leave_types", "employee_leave_balances", "leave_records",
        "leave_balance_transactions",
    }
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if expected_tables.issubset(existing_tables):
        return
    if expected_tables.intersection(existing_tables):
        raise RuntimeError("Partial leave schema detected; refusing an unsafe upgrade.")

    op.create_table(
        "leave_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("paid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("deducts_balance", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint("code <> ''", name="ck_leave_types_code_nonempty"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "employee_leave_balances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("leave_type_id", sa.Integer(), nullable=False),
        sa.Column("opening_balance", sa.Numeric(8, 2), nullable=False),
        sa.Column("current_balance", sa.Numeric(8, 2), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("opening_balance >= 0", name="ck_leave_balance_opening_nonnegative"),
        sa.CheckConstraint("current_balance >= 0", name="ck_leave_balance_current_nonnegative"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["leave_type_id"], ["leave_types.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("employee_id", "leave_type_id", name="uq_employee_leave_balance"),
    )
    op.create_index("ix_employee_leave_balances_employee_id", "employee_leave_balances", ["employee_id"])
    op.create_index("ix_employee_leave_balances_leave_type_id", "employee_leave_balances", ["leave_type_id"])
    op.create_table(
        "leave_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("leave_type_id", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("days", sa.Numeric(8, 2), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="confirmed"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("end_date >= start_date", name="ck_leave_records_date_order"),
        sa.CheckConstraint("days > 0", name="ck_leave_records_days_positive"),
        sa.CheckConstraint("status IN ('confirmed', 'cancelled')", name="ck_leave_records_status"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["leave_type_id"], ["leave_types.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_leave_records_employee_id", "leave_records", ["employee_id"])
    op.create_index("ix_leave_records_leave_type_id", "leave_records", ["leave_type_id"])
    op.create_index("ix_leave_records_start_date", "leave_records", ["start_date"])
    op.create_index("ix_leave_records_end_date", "leave_records", ["end_date"])
    op.create_table(
        "leave_balance_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("leave_type_id", sa.Integer(), nullable=False),
        sa.Column("leave_record_id", sa.Integer(), nullable=True),
        sa.Column("change_amount", sa.Numeric(8, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(8, 2), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["leave_type_id"], ["leave_types.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["leave_record_id"], ["leave_records.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_leave_balance_transactions_employee_id", "leave_balance_transactions", ["employee_id"])
    op.create_index("ix_leave_balance_transactions_leave_type_id", "leave_balance_transactions", ["leave_type_id"])


def downgrade():
    op.drop_index("ix_leave_balance_transactions_leave_type_id", table_name="leave_balance_transactions")
    op.drop_index("ix_leave_balance_transactions_employee_id", table_name="leave_balance_transactions")
    op.drop_table("leave_balance_transactions")
    op.drop_index("ix_leave_records_end_date", table_name="leave_records")
    op.drop_index("ix_leave_records_start_date", table_name="leave_records")
    op.drop_index("ix_leave_records_leave_type_id", table_name="leave_records")
    op.drop_index("ix_leave_records_employee_id", table_name="leave_records")
    op.drop_table("leave_records")
    op.drop_index("ix_employee_leave_balances_leave_type_id", table_name="employee_leave_balances")
    op.drop_index("ix_employee_leave_balances_employee_id", table_name="employee_leave_balances")
    op.drop_table("employee_leave_balances")
    op.drop_table("leave_types")
