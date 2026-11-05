"""Add durable employee numbering and administrator session invalidation."""
import re

from alembic import op
import sqlalchemy as sa

revision = "0002_platform_state"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "admin_accounts",
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "employee_code_counters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_number", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_employee_code_counter_singleton"),
        sa.CheckConstraint("last_number >= 0", name="ck_employee_code_counter_nonnegative"),
    )

    # Seed above every existing MY-NNNN code so existing identifiers are never reused.
    connection = op.get_bind()
    codes = connection.execute(sa.text("SELECT code FROM employees")).scalars()
    highest = 0
    for code in codes:
        match = re.fullmatch(r"MY-(\d+)", code or "")
        if match:
            highest = max(highest, int(match.group(1)))
    connection.execute(
        sa.text("INSERT INTO employee_code_counters (id, last_number) VALUES (1, :n)"),
        {"n": highest},
    )


def downgrade():
    op.drop_table("employee_code_counters")
    op.drop_column("admin_accounts", "session_version")
