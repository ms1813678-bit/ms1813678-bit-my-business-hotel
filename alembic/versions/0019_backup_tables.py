"""backup system settings and backup runs

Revision ID: 0019_backup_tables
Revises: 0018_advances_and_staff_actions
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0019_backup_tables"
down_revision = "0018_advances_and_staff_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "system_settings" not in tables:
        op.create_table(
            "system_settings",
            sa.Column("key", sa.String(80), primary_key=True),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column("updated_by", sa.String(80), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "backup_runs" not in tables:
        op.create_table(
            "backup_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("backup_type", sa.String(20), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("archive_path", sa.String(500), nullable=True),
            sa.Column("sha256", sa.String(64), nullable=True),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("initiated_by", sa.String(80), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.CheckConstraint(
                "backup_type IN ('daily', 'weekly', 'quarterly', 'manual', 'pre_restore')",
                name="ck_backup_run_type",
            ),
            sa.CheckConstraint("status IN ('success', 'failed')", name="ck_backup_run_status"),
            sa.CheckConstraint("size_bytes >= 0", name="ck_backup_run_size_nonnegative"),
        )
        op.create_index("ix_backup_runs_backup_type", "backup_runs", ["backup_type"])


def downgrade() -> None:
    op.drop_table("backup_runs")
    op.drop_table("system_settings")
