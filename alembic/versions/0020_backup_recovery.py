"""Create system settings and verified backup history."""
from alembic import op
import sqlalchemy as sa


revision = "0020_backup_recovery"
down_revision = "0019_payroll_runs"
branch_labels = None
depends_on = None


def upgrade():
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "system_settings" not in existing_tables:
        op.create_table(
            "system_settings",
            sa.Column("key", sa.String(length=100), primary_key=True),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column("updated_by", sa.String(length=80), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "backup_runs" not in existing_tables:
        op.create_table(
            "backup_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("backup_type", sa.String(length=20), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("archive_path", sa.Text(), nullable=True),
            sa.Column("sha256", sa.String(length=64), nullable=True),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("initiated_by", sa.String(length=80), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.CheckConstraint(
                "backup_type IN ('daily', 'weekly', 'quarterly', 'manual', 'pre_restore')",
                name="ck_backup_runs_type",
            ),
            sa.CheckConstraint("status IN ('success', 'failed')", name="ck_backup_runs_status"),
            sa.CheckConstraint("size_bytes >= 0", name="ck_backup_runs_size"),
        )
        op.create_index("ix_backup_runs_backup_type", "backup_runs", ["backup_type"])
        op.create_index("ix_backup_runs_status", "backup_runs", ["status"])
        op.create_index("ix_backup_runs_created_at", "backup_runs", ["created_at"])


def downgrade():
    # Backup history and system settings are operational records; preserve them on rollback.
    pass
