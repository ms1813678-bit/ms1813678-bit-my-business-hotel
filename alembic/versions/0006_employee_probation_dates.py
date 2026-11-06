"""Add employee probation dates, optional photo path, and data change requests."""
from alembic import op
import sqlalchemy as sa

revision = "0006_employee_probation_dates"
down_revision = "0005_merge_code_heads"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("employees")}
    if "probation_start_date" not in existing:
        op.add_column("employees", sa.Column("probation_start_date", sa.Date(), nullable=True))
    if "probation_end_date" not in existing:
        op.add_column("employees", sa.Column("probation_end_date", sa.Date(), nullable=True))
    if "photo_path" not in existing:
        op.add_column("employees", sa.Column("photo_path", sa.String(length=255), nullable=True))
    if "employee_data_change_requests" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "employee_data_change_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
            sa.Column("field_name", sa.String(length=40), nullable=False),
            sa.Column("requested_value", sa.Text(), nullable=False),
            sa.Column("request_reason", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("decision_reason", sa.Text(), nullable=True),
            sa.Column("effective_date", sa.Date(), nullable=True),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reviewed_by", sa.String(length=80), nullable=True),
        )
        op.create_index(
            "ix_employee_data_change_requests_employee_id",
            "employee_data_change_requests",
            ["employee_id"],
        )


def downgrade():
    # Retain employee photos, probation dates and submitted requests as historical
    # records. A version downgrade must not erase HR data.
    pass
