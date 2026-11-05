"""Preserve employee service periods without inventing former-employee dates."""
from alembic import op
import sqlalchemy as sa

revision = "0003_employee_service_periods"
down_revision = "0002_platform_state"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
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

    # Stop rather than modifying existing service history if it already violates
    # the one-open-period invariant.
    duplicate = connection.execute(sa.text("""
        SELECT employee_id
        FROM employee_service_periods
        WHERE end_date IS NULL
        GROUP BY employee_id
        HAVING COUNT(*) > 1
        LIMIT 1
    """)).scalar_one_or_none()
    if duplicate is not None:
        raise RuntimeError(
            f"Employee {duplicate} already has multiple open service periods. "
            "Resolve the conflicting history before applying this migration; no rows were changed."
        )

    indexes = {item["name"] for item in sa.inspect(connection).get_indexes("employee_service_periods")}
    if "uq_employee_one_open_service_period" not in indexes:
        op.create_index(
            "uq_employee_one_open_service_period",
            "employee_service_periods",
            ["employee_id"],
            unique=True,
            postgresql_where=sa.text("end_date IS NULL"),
        )

    connection.execute(sa.text("""
        INSERT INTO employee_service_periods
            (employee_id, start_date, end_date, start_reason)
        SELECT e.id, e.hire_date, NULL, 'Migrated active employment'
        FROM employees AS e
        WHERE lower(trim(coalesce(e.status, ''))) = 'active'
          AND NOT EXISTS (
              SELECT 1 FROM employee_service_periods AS p
              WHERE p.employee_id = e.id
          )
    """))


def downgrade():
    # Service-period history is retained intentionally. A version downgrade must
    # never erase employment history or remove its database-level uniqueness guard.
    pass
