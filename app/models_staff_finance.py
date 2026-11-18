"""Salary advance and staff action persistence models."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SalaryAdvance(Base):
    """Administrator-recorded salary advance; deducted in full from the following payroll month."""
    __tablename__ = "salary_advances"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_salary_advance_amount_positive"),
        CheckConstraint(
            "status IN ('active', 'deducted', 'cancelled')",
            name="ck_salary_advance_status",
        ),
        CheckConstraint(
            "request_day BETWEEN 15 AND 22",
            name="ck_salary_advance_request_window_day",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), index=True)
    request_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    request_day: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payroll_year: Mapped[int] = mapped_column(Integer, nullable=False)
    payroll_month: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    cancelled_by: Mapped[str | None] = mapped_column(String(80))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    deducted_payroll_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("payroll_lines.id", ondelete="RESTRICT")
    )


class StaffAction(Base):
    """Sanctions and rewards recorded by the administrator with optional payroll amount."""
    __tablename__ = "staff_actions"
    __table_args__ = (
        CheckConstraint(
            "action_kind IN ('sanction', 'reward')",
            name="ck_staff_action_kind",
        ),
        CheckConstraint(
            "status IN ('active', 'cancelled')",
            name="ck_staff_action_status",
        ),
        CheckConstraint(
            "amount IS NULL OR amount >= 0",
            name="ck_staff_action_amount_nonnegative",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), index=True)
    action_kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    action_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    payroll_year: Mapped[int | None] = mapped_column(Integer)
    payroll_month: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    cancelled_by: Mapped[str | None] = mapped_column(String(80))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
