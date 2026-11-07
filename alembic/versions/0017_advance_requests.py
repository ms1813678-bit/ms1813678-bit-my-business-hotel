"""Create advance requests with explicit next-month repayment tracking."""
from alembic import op
import sqlalchemy as sa

revision = "0017_advance_requests"
down_revision = "0016_leave_management"
branch_labels = None
depends_on = None


def upgrade():
    if "advance_requests" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "advance_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("request_date", sa.Date(), nullable=False),
            sa.Column("salary_month", sa.Date(), nullable=False),
            sa.Column("repayment_month", sa.Date(), nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("created_by", sa.String(80), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("decided_by", sa.String(80), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("decision_reason", sa.Text(), nullable=True),
            sa.Column("disbursed_by", sa.String(80), nullable=True),
            sa.Column("disbursed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deducted_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint("amount > 0", name="ck_advance_requests_amount_positive"),
            sa.CheckConstraint(
                "status IN ('pending', 'approved', 'rejected', 'disbursed', 'deducted', 'cancelled')",
                name="ck_advance_requests_status",
            ),
            sa.CheckConstraint(
                "repayment_month > salary_month",
                name="ck_advance_requests_repayment_after_salary_month",
            ),
        )
        op.create_index("ix_advance_requests_employee_id", "advance_requests", ["employee_id"])
        op.create_index("ix_advance_requests_request_date", "advance_requests", ["request_date"])
        op.create_index("ix_advance_requests_salary_month", "advance_requests", ["salary_month"])
        op.create_index("ix_advance_requests_repayment_month", "advance_requests", ["repayment_month"])


def downgrade():
    # Advance and repayment history is financial data and must survive rollback.
    pass
