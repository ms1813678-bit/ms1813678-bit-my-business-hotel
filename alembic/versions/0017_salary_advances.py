"""Create administrator-managed salary advances."""
from alembic import op
import sqlalchemy as sa


revision = "0017_salary_advances"
down_revision = "0016_leave_management"
branch_labels = None
depends_on = None


def upgrade():
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "advance_requests" not in existing_tables:
        op.create_table(
            "advance_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("request_date", sa.Date(), nullable=False),
            sa.Column("payroll_month", sa.Date(), nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("salary_basis", sa.Numeric(12, 2), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("created_by", sa.String(length=80), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("approved_by", sa.String(length=80), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejected_by", sa.String(length=80), nullable=True),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("paid_by", sa.String(length=80), nullable=True),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deducted_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint("amount > 0", name="ck_advance_requests_amount_positive"),
            sa.CheckConstraint("salary_basis > 0", name="ck_advance_requests_salary_positive"),
            sa.CheckConstraint(
                "status IN ('pending', 'approved', 'rejected', 'paid', 'deducted')",
                name="ck_advance_requests_status",
            ),
        )
        op.create_index("ix_advance_requests_employee_id", "advance_requests", ["employee_id"])
        op.create_index("ix_advance_requests_request_date", "advance_requests", ["request_date"])
        op.create_index("ix_advance_requests_payroll_month", "advance_requests", ["payroll_month"])


def downgrade():
    # Keep financial advance records and their audit trail across rollbacks.
    pass
