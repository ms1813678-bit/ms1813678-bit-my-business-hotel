"""Add optional employee probation dates.

Revision ID: 0006_employee_probation_dates
Revises: 0005_merge_code_heads
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_employee_probation_dates"
down_revision = "0005_merge_code_heads"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("employees", sa.Column("probation_start_date", sa.Date(), nullable=True))
    op.add_column("employees", sa.Column("probation_end_date", sa.Date(), nullable=True))


def downgrade():
    # Both columns are optional metadata; removing them does not delete employee
    # records, but a downgrade would discard explicitly entered probation dates.
    op.drop_column("employees", "probation_end_date")
    op.drop_column("employees", "probation_start_date")
