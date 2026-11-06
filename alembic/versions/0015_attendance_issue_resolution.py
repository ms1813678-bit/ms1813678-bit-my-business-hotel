"""Allow explicit HR resolution of unknown attendance employee identities."""
from alembic import op
import sqlalchemy as sa


revision = "0015_attendance_issue_resolution"
down_revision = "0014_attendance_source_files"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("attendance_import_issues", sa.Column("resolved_employee_id", sa.Integer(), nullable=True))
    op.add_column("attendance_import_issues", sa.Column("resolved_by", sa.String(length=80), nullable=True))
    op.add_column("attendance_import_issues", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_attendance_import_issues_resolved_employee",
        "attendance_import_issues", "employees",
        ["resolved_employee_id"], ["id"],
    )


def downgrade():
    op.drop_constraint(
        "fk_attendance_import_issues_resolved_employee",
        "attendance_import_issues", type_="foreignkey",
    )
    op.drop_column("attendance_import_issues", "resolved_at")
    op.drop_column("attendance_import_issues", "resolved_by")
    op.drop_column("attendance_import_issues", "resolved_employee_id")
