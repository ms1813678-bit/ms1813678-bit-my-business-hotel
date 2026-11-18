"""Allow explicit HR resolution of unknown attendance employee identities."""
from alembic import op
import sqlalchemy as sa


revision = "0015_attendance_issue_resolution"
down_revision = "0014_attendance_source_files"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("attendance_import_issues")}
    foreign_keys = inspector.get_foreign_keys("attendance_import_issues")
    has_employee_fk = any(
        "resolved_employee_id" in foreign_key.get("constrained_columns", [])
        and foreign_key.get("referred_table") == "employees"
        for foreign_key in foreign_keys
    )

    with op.batch_alter_table("attendance_import_issues") as batch_op:
        if "resolved_employee_id" not in columns:
            batch_op.add_column(sa.Column("resolved_employee_id", sa.Integer(), nullable=True))
        if "resolved_by" not in columns:
            batch_op.add_column(sa.Column("resolved_by", sa.String(length=80), nullable=True))
        if "resolved_at" not in columns:
            batch_op.add_column(sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
        if not has_employee_fk:
            batch_op.create_foreign_key(
                "fk_attendance_import_issues_resolved_employee",
                "employees",
                ["resolved_employee_id"],
                ["id"],
            )


def downgrade():
    # Keep HR resolution records intact; this revision is intentionally irreversible.
    pass
