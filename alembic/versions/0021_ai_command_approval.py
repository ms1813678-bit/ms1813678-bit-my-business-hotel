"""Add local AI approval tokens and conversation memory."""
from alembic import op
import sqlalchemy as sa


revision = "0021_ai_command_approval"
down_revision = "0020_backup_recovery"
branch_labels = None
depends_on = None


def upgrade():
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "ai_command_approvals" not in existing_tables:
        op.create_table(
            "ai_command_approvals",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
            sa.Column("actor", sa.String(length=80), nullable=False, index=True),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("plan_json", sa.Text(), nullable=False),
            sa.Column("plan_hash", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("failure_reason", sa.Text(), nullable=True),
            sa.CheckConstraint(
                "status IN ('pending','executing','consumed','rejected','expired','invalidated','failed')",
                name="ck_ai_command_approval_status",
            ),
        )
        op.create_index(
            "ix_ai_command_approvals_status_expires_at",
            "ai_command_approvals",
            ["status", "expires_at"],
        )
    if "ai_conversation_messages" not in existing_tables:
        op.create_table(
            "ai_conversation_messages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("actor", sa.String(length=80), nullable=False, index=True),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("role IN ('user','assistant','system')", name="ck_ai_conversation_role"),
        )
        op.create_index(
            "ix_ai_conversation_messages_actor_id",
            "ai_conversation_messages",
            ["actor", "id"],
        )


def downgrade():
    # Preserve AI approval/audit history on rollback.
    pass
