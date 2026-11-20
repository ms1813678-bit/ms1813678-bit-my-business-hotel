"""Add separate before/after matching margins to shift patterns."""
from alembic import op
import sqlalchemy as sa


revision = "0008_shift_matching_margins"
down_revision = "0007_attendance_shift_foundation"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("shift_patterns")}

    if "match_before_minutes" not in columns:
        op.add_column(
            "shift_patterns",
            sa.Column("match_before_minutes", sa.Integer(), nullable=True),
        )
    if "match_after_minutes" not in columns:
        op.add_column(
            "shift_patterns",
            sa.Column("match_after_minutes", sa.Integer(), nullable=True),
        )

    # Backfill only rows that have an explicitly configured legacy window.
    # Also preserve already-migrated values if this revision is re-applied
    # after a downgrade whose policy intentionally retains HR configuration.
    op.execute(
        "UPDATE shift_patterns SET "
        "match_before_minutes = LEAST(match_window_minutes, 120) "
        "WHERE match_before_minutes IS NULL AND match_window_minutes IS NOT NULL"
    )
    op.execute(
        "UPDATE shift_patterns SET "
        "match_after_minutes = LEAST(match_window_minutes, 120) "
        "WHERE match_after_minutes IS NULL AND match_window_minutes IS NOT NULL"
    )

    constraints = {
        item["name"] for item in sa.inspect(bind).get_check_constraints("shift_patterns")
    }
    if "ck_shift_patterns_match_before_range" not in constraints:
        op.create_check_constraint(
            "ck_shift_patterns_match_before_range",
            "shift_patterns",
            "match_before_minutes BETWEEN 0 AND 120",
        )
    if "ck_shift_patterns_match_after_range" not in constraints:
        op.create_check_constraint(
            "ck_shift_patterns_match_after_range",
            "shift_patterns",
            "match_after_minutes BETWEEN 0 AND 120",
        )


def downgrade():
    # Retain HR configuration. upgrade() is intentionally idempotent so a
    # later re-upgrade does not attempt to recreate retained columns/constraints.
    pass
