"""Add explicit advance type so the 25% rule applies only to regular advances."""
from alembic import op
import sqlalchemy as sa

revision = "0022_advance_type"
down_revision = "0021_advance_installments"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("advance_requests")}
    if "advance_type" not in columns:
        op.add_column(
            "advance_requests",
            sa.Column("advance_type", sa.String(20), nullable=False, server_default="regular"),
        )
    constraints = {c["name"] for c in inspector.get_check_constraints("advance_requests")}
    if "ck_advance_requests_type" not in constraints:
        op.create_check_constraint(
            "ck_advance_requests_type",
            "advance_requests",
            "advance_type IN ('regular', 'installment')",
        )


def downgrade():
    # Preserve financial history; do not remove the type column.
    pass
