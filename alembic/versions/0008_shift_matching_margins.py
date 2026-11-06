"""Add separate before/after matching margins to shift patterns."""
from alembic import op
import sqlalchemy as sa


revision = "0008_shift_matching_margins"
down_revision = "0007_attendance_shift_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "shift_patterns",
        sa.Column("match_before_minutes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "shift_patterns",
        sa.Column("match_after_minutes", sa.Integer(), nullable=True),
    )
    # Preserve existing symmetric configuration where it was explicitly set.
    op.execute(
        "UPDATE shift_patterns SET "
        "match_before_minutes = LEAST(match_window_minutes, 120), "
        "match_after_minutes = LEAST(match_window_minutes, 120) "
        "WHERE match_window_minutes IS NOT NULL"
    )
    op.create_check_constraint(
        "ck_shift_patterns_match_before_range",
        "shift_patterns",
        "match_before_minutes BETWEEN 0 AND 120",
    )
    op.create_check_constraint(
        "ck_shift_patterns_match_after_range",
        "shift_patterns",
        "match_after_minutes BETWEEN 0 AND 120",
    )


def downgrade():
    # Keep the new columns on downgrade to avoid discarding HR configuration.
    pass
