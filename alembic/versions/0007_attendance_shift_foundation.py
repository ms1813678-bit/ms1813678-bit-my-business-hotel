"""Create configurable shift patterns and security shift assignments."""
from alembic import op
import sqlalchemy as sa


revision = "0007_attendance_shift_foundation"
down_revision = "0006_employee_probation_dates"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "shift_patterns" not in tables:
        op.create_table(
            "shift_patterns",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("department_id", sa.Integer(), sa.ForeignKey("departments.id"), nullable=False),
            sa.Column("code", sa.String(length=40), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("start_time", sa.Time(), nullable=False),
            sa.Column("end_time", sa.Time(), nullable=False),
            sa.Column("match_window_minutes", sa.Integer(), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.UniqueConstraint("department_id", "code", name="uq_shift_patterns_department_code"),
            sa.CheckConstraint(
                "match_window_minutes IS NULL OR match_window_minutes >= 0",
                name="ck_shift_patterns_match_window_nonnegative",
            ),
        )
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("shift_patterns")}
    if "ix_shift_patterns_department_id" not in indexes:
        op.create_index("ix_shift_patterns_department_id", "shift_patterns", ["department_id"])

    if "security_shift_assignments" not in tables:
        op.create_table(
            "security_shift_assignments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
            sa.Column("work_date", sa.Date(), nullable=False),
            sa.Column("shift_pattern_id", sa.Integer(), sa.ForeignKey("shift_patterns.id"), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("employee_id", "work_date", name="uq_security_shift_employee_date"),
        )
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("security_shift_assignments")}
    if "ix_security_shift_assignments_employee_id" not in indexes:
        op.create_index("ix_security_shift_assignments_employee_id", "security_shift_assignments", ["employee_id"])
    if "ix_security_shift_assignments_work_date" not in indexes:
        op.create_index("ix_security_shift_assignments_work_date", "security_shift_assignments", ["work_date"])


def downgrade():
    # Retain operational configuration and assignment history on downgrade.
    pass
