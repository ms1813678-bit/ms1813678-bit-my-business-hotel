"""Use a non-transactional PostgreSQL sequence for never-reused MY codes."""
from alembic import op
import sqlalchemy as sa

revision = "0004_non_reusable_employee_codes"
down_revision = "0003_employee_service_periods"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        raise RuntimeError("Employee code reservations require PostgreSQL sequences.")

    op.execute("""
        CREATE SEQUENCE IF NOT EXISTS employee_code_number_seq
        AS BIGINT START WITH 1 INCREMENT BY 1 MINVALUE 1 NO MAXVALUE CACHE 1
    """)

    connection.execute(sa.text("""
        WITH current_values AS (
            SELECT GREATEST(
                COALESCE((SELECT last_number FROM employee_code_counters WHERE id = 1), 0),
                COALESCE((
                    SELECT MAX(substring(code FROM 4)::BIGINT)
                    FROM employees
                    WHERE code ~ '^MY-[0-9]+$'
                ), 0),
                COALESCE((
                    SELECT CASE WHEN is_called THEN last_value ELSE last_value - 1 END
                    FROM employee_code_number_seq
                ), 0)
            ) AS highest_reserved
        )
        SELECT setval(
            'employee_code_number_seq',
            GREATEST(highest_reserved, 1),
            highest_reserved > 0
        )
        FROM current_values
    """))


def downgrade():
    # Never drop/reset this sequence: PostgreSQL sequence values are reservation
    # history, and reusing them after rollback would violate the employee-code rule.
    pass
