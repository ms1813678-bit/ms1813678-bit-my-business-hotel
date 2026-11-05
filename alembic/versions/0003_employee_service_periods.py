"""Preserve each employee's separate service periods."""
from alembic import op
import sqlalchemy as sa

revision = "0003_employee_service_periods"
down_revision = "0002_platform_state"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "employee_service_periods",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("start_reason", sa.Text(), nullable=False, server_default="Initial employment"),
        sa.Column("end_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="ck_employee_service_period_dates",
        ),
    )
    op.create_index(
        "ix_employee_service_periods_employee_id",
        "employee_service_periods",
        ["employee_id"],
    )
    connection = op.get_bind()
    connection.execute(sa.text("""
        INSERT INTO employee_service_periods
            (employee_id, start_date, end_date, start_reason)
        SELECT id, hire_date, NULL, 'Migrated existing employment'
        FROM employees
    """))


def downgrade():
    op.drop_index("ix_employee_service_periods_employee_id", table_name="employee_service_periods")
    op.drop_table("employee_service_periods")
