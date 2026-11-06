"""Allow explicit HR resolution of unknown attendance employee identities."""
from alembic import op
import sqlalchemy as sa


revision = "0015_attendance_issue_resolution"
down_revision = "0014_attendance_source_files"
branch_labels = None
depends_on = None


def upgrade():
    # Batch mode also supports SQLite deployments used by the Windows installer.
    with op.batch_alter_table("attendance_import_issues") as batch_op:
        batch_op.add_column(sa.Column("resolved_employee_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("resolved_by", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_attendance_import_issues_resolved_employee",
            "employees",
            ["resolved_employee_id"],
            ["id"],
        )


def downgrade():
    with op.batch_alter_table("attendance_import_issues") as batch_op:
        batch_op.drop_constraint(
            "fk_attendance_import_issues_resolved_employee", type_="foreignkey"
        )
        batch_op.drop_column("resolved_at")
        batch_op.drop_column("resolved_by")
        batch_op.drop_column("resolved_employee_id")
