"""Create immutable salary advance installment ledger.

The ledger is deliberately separate from AdvanceRequest so payroll can consume
individual salary-month deductions idempotently.
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_advance_installments"
down_revision = "0020_backup_recovery"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "advance_installments" in inspector.get_table_names():
        return
    op.create_table(
        "advance_installments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("advance_request_id", sa.Integer(), sa.ForeignKey("advance_requests.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("installment_number", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("payroll_month", sa.Date(), nullable=False),
        sa.Column("scheduled_payment_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("applied_payroll_run_id", sa.Integer(), sa.ForeignKey("payroll_runs.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversal_reference", sa.String(200), nullable=True),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("advance_request_id", "installment_number", name="uq_advance_installments_number"),
        sa.UniqueConstraint("advance_request_id", "payroll_month", name="uq_advance_installments_payroll_month"),
        sa.CheckConstraint("installment_number >= 1", name="ck_advance_installments_number"),
        sa.CheckConstraint("amount > 0", name="ck_advance_installments_amount"),
        sa.CheckConstraint("status IN ('scheduled', 'applied', 'reversed', 'cancelled')", name="ck_advance_installments_status"),
    )
    op.create_index("ix_advance_installments_advance_request_id", "advance_installments", ["advance_request_id"])
    op.create_index("ix_advance_installments_payroll_month", "advance_installments", ["payroll_month"])
    op.create_index("ix_advance_installments_applied_payroll_run_id", "advance_installments", ["applied_payroll_run_id"])


def downgrade():
    # Financial history must not be erased by routine downgrade.
    pass
