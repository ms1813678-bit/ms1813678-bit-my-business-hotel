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
            sa.Column("last_number", sa.Integer(), nullable=False),
            sa.CheckConstraint("id = 1", name="ck_employee_code_counter_singleton"),
            sa.CheckConstraint("last_number >= 0", name="ck_employee_code_counter_nonnegative"),
        )

    # Seed above every existing MY-NNNN code; never overwrite a persisted counter.
    codes = connection.execute(sa.text("SELECT code FROM employees")).scalars()
    highest = 0
    for code in codes:
        match = re.fullmatch(r"MY-(\d+)", code or "")
        if match:
            highest = max(highest, int(match.group(1)))
    counter_exists = connection.execute(
        sa.text("SELECT 1 FROM employee_code_counters WHERE id = 1")
    ).scalar_one_or_none()
    if counter_exists is None:
        connection.execute(
            sa.text("INSERT INTO employee_code_counters (id, last_number) VALUES (1, :n)"),
            {"n": highest},
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "employee_code_counters" in inspector.get_table_names():
        op.drop_table("employee_code_counters")
    admin_columns = {column["name"] for column in inspector.get_columns("admin_accounts")}
    if "session_version" in admin_columns:
        op.drop_column("admin_accounts", "session_version")
