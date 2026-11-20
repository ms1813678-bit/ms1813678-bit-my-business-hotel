"""Create employee sanctions and rewards history."""
from alembic import op
import sqlalchemy as sa


revision = "0018_staff_actions"
down_revision = "0017_salary_advances"
branch_labels = None
depends_on = None


def upgrade():
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "staff_actions" not in existing_tables:
        op.create_table(
            "staff_actions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("kind", sa.String(length=20), nullable=False),
            sa.Column("category", sa.String(length=40), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("is_financial", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("amount", sa.Numeric(12, 2), nullable=True),
            sa.Column("payroll_month", sa.Date(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("created_by", sa.String(length=80), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("approved_by", sa.String(length=80), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_by", sa.String(length=80), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancellation_reason", sa.Text(), nullable=True),
            sa.Column("acknowledgement_status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("acknowledgement_recorded_by", sa.String(length=80), nullable=True),
            sa.Column("acknowledgement_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("acknowledgement_note", sa.Text(), nullable=True),
            sa.Column("payroll_applied_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint("kind IN ('sanction', 'reward')", name="ck_staff_actions_kind"),
            sa.CheckConstraint("amount IS NULL OR amount > 0", name="ck_staff_actions_amount_positive"),
            sa.CheckConstraint("status IN ('draft', 'approved', 'cancelled')", name="ck_staff_actions_status"),
            sa.CheckConstraint(
                "acknowledgement_status IN ('pending', 'viewed', 'declined')",
                name="ck_staff_actions_ack_status",
            ),
            sa.CheckConstraint(
                "(is_financial = TRUE AND amount IS NOT NULL AND payroll_month IS NOT NULL) "
                "OR (is_financial = FALSE AND amount IS NULL AND payroll_month IS NULL)",
                name="ck_staff_actions_financial_fields",
            ),
        )
        op.create_index("ix_staff_actions_employee_id", "staff_actions", ["employee_id"])
        op.create_index("ix_staff_actions_kind", "staff_actions", ["kind"])
        op.create_index("ix_staff_actions_payroll_month", "staff_actions", ["payroll_month"])


def downgrade():
    # Preserve employee disciplinary and reward history across rollbacks.
    pass
