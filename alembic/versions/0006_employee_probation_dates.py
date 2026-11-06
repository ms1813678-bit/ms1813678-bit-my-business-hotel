"""Add optional employee probation dates without discarding them on downgrade."""
from alembic import op
import sqlalchemy as sa

revision = "0006_employee_probation_dates"
down_revision = "0005_merge_code_heads"
branch_labels = None
depends_on = None


def upgrade():
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("employees")}
    if "probation_start_date" not in existing:
        op.add_column("employees", sa.Column("probation_start_date", sa.Date(), nullable=True))
    if "probation_end_date" not in existing:
        op.add_column("employees", sa.Column("probation_end_date", sa.Date(), nullable=True))


def downgrade():
    # Preserve recorded probation history. Like service-period history, this
    # metadata is intentionally retained when Alembic's version is downgraded.
    pass
