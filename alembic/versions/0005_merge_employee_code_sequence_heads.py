"""Merge the two employee-code sequence migration branches.

Both 0004 revisions establish the same durable PostgreSQL sequence. Keep both
historical revisions so databases that have applied either branch can upgrade
safely, then converge the graph on one Alembic head.
"""
revision = "0005_merge_code_heads"
down_revision = (
    "0004_employee_code_sequence",
    "0004_non_reusable_employee_codes",
)
branch_labels = None
depends_on = None


def upgrade():
    # The parent revisions perform the idempotent sequence setup and high-water
    # mark reconciliation. This revision only joins their histories.
    pass


def downgrade():
    # Keep the sequence and its reservation history; downgrade must not rewind it.
    pass
