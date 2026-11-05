"""Add a non-transactional PostgreSQL sequence for permanent employee-code reservations.

The sequence intentionally survives downgrade. PostgreSQL sequence values are not
rolled back with the surrounding transaction, so a reserved code can never be
reissued after a transaction rollback.
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_employee_code_sequence"
down_revision = "0003_employee_service_periods"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()

    # Keep the legacy counter row and employee rows stable while establishing the
    # sequence high-water mark. The application should be stopped during migrations.
    connection.execute(sa.text("LOCK TABLE employee_code_counters IN ACCESS EXCLUSIVE MODE"))

    connection.execute(sa.text("""
        CREATE SEQUENCE IF NOT EXISTS employee_code_number_seq
            AS BIGINT
            MINVALUE 1
            START WITH 1
            INCREMENT BY 1
            NO CYCLE
    """))

    highest_employee_code = connection.execute(sa.text("""
        SELECT COALESCE(MAX(substring(code FROM 4)::BIGINT), 0)
        FROM employees
        WHERE code ~ '^MY-[0-9]+$'
    """)).scalar_one()

    counter_value = connection.execute(sa.text("""
        SELECT last_number
        FROM employee_code_counters
        WHERE id = 1
    """)).scalar_one_or_none()

    highest_reserved = max(highest_employee_code, counter_value or 0)
    sequence_value, sequence_called = connection.execute(sa.text("""
        SELECT last_value, is_called
        FROM employee_code_number_seq
    """)).one()

    # Advance only. Never reset an existing sequence backwards on re-upgrade.
    if highest_reserved > sequence_value or (
        highest_reserved == sequence_value and highest_reserved > 0 and not sequence_called
    ):
        connection.execute(
            sa.text("SELECT setval('employee_code_number_seq', :value, TRUE)"),
            {"value": highest_reserved},
        )


def downgrade():
    # Do not drop or rewind the sequence. A downgrade must not make a previously
    # reserved employee code available for reuse after a later re-upgrade.
    pass
