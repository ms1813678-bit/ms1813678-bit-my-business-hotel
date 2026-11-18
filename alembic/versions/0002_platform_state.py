"""Add durable employee numbering and administrator session invalidation."""
import re

from alembic import op
import sqlalchemy as sa

revision = "0002_platform_state"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    admin_columns = {column["name"] for column in inspector.get_columns("admin_accounts")}
    if "session_version" not in admin_columns:
        op.add_column(
            "admin_accounts",
            sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"),
        )

    if "employee_code_counters" not in inspector.get_table_names():
        op.create_table(
            "employee_code_counters",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("last_number", sa.Integer(), nullable=False, server_default="0"),
            sa.CheckConstraint("id = 1", name="ck_employee_code_counter_singleton"),
            sa.CheckConstraint("last_number >= 0", name="ck_employee_code_counter_nonnegative"),
        )

    highest_code = 0
    for code in connection.execute(sa.text("SELECT code FROM employees")).scalars():
        match = re.fullmatch(r"MY-(\d+)", code or "")
        if match:
            highest_code = max(highest_code, int(match.group(1)))

    counter = connection.execute(
        sa.text("SELECT last_number FROM employee_code_counters WHERE id = 1")
    ).scalar_one_or_none()
    if counter is None:
        connection.execute(
            sa.text("INSERT INTO employee_code_counters (id, last_number) VALUES (1, :n)"),
            {"n": highest_code},
        )
    elif counter < highest_code:
        connection.execute(
            sa.text(
                "UPDATE employee_code_counters "
                "SET last_number = :n WHERE id = 1 AND last_number < :n"
            ),
            {"n": highest_code},
        )


def downgrade():
    # Keep the counter and session_version permanently; dropping either can cause
    # employee-code reuse or make previously invalidated sessions valid again.
    pass
