"""Preserve each employee's separate service periods."""
from alembic import op
import sqlalchemy as sa

revision = "0003_employee_service_periods"
down_revision = "0002_platform_state"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    if "employee_service_periods" not in tables:
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

    indexes = {item["name"] for item in sa.inspect(connection).get_indexes("employee_service_periods")}
    if "ix_employee_service_periods_employee_id" not in indexes:
        op.create_index(
            "ix_employee_service_periods_employee_id",
            "employee_service_periods",
            ["employee_id"],
        )

    # Populate legacy employees once, without duplicating periods already created.
    existing_indexes = {item["name"] for item in sa.inspect(connection).get_indexes("employee_service_periods")}
    if "uq_employee_one_open_service_period" not in existing_indexes:
        op.create_index(
            "uq_employee_one_open_service_period",
            "employee_service_periods", ["employee_id"], unique=True,
            postgresql_where=sa.text("end_date IS NULL"),
            sqlite_where=sa.text("end_date IS NULL"),
        )

    connection.execute(sa.text("""
        INSERT INTO employee_service_periods
            (employee_id, start_date, end_date, start_reason)
        SELECT e.id, e.hire_date, NULL, 'Migrated existing employment'
        FROM employees AS e
        WHERE NOT EXISTS (
            SELECT 1 FROM employee_service_periods AS p
            WHERE p.employee_id = e.id
        )
    """))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "employee_service_periods" in inspector.get_table_names():
        indexes = {item["name"] for item in inspector.get_indexes("employee_service_periods")}
        if "ix_employee_service_periods_employee_id" in indexes:
            op.drop_index("ix_employee_service_periods_employee_id", table_name="employee_service_periods")
        if "uq_employee_one_open_service_period" in indexes:
            op.drop_index("uq_employee_one_open_service_period", table_name="employee_service_periods")
        op.drop_table("employee_service_periods")
