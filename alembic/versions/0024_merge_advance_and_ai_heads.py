"""Merge advance installment path and AI approval path into a single head.

Revision ID: 0024_merge_advance_and_ai_heads
Revises: 0023_overtime_approval, 0021_ai_command_approval
Create Date: 2026-11-17

This is an empty merge revision. Both parent heads already create the
required schema (advance installments + AI approval tables). No further
DDL is applied here.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "0024_merge_advance_and_ai_heads"
down_revision = ("0023_overtime_approval", "0021_ai_command_approval")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Schema already applied by the two parent heads.
    # Intentionally empty to avoid re-creating tables or constraints.
    pass


def downgrade() -> None:
    # Empty merge: nothing to undo at this node.
    # Parent revisions retain their own (also non-destructive) downgrades.
    pass
