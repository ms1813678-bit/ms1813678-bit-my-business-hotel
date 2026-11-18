"""Salary advances and staff sanctions/rewards with explicit administrator control."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Employee, SalaryAdvance, StaffAction
from app.payroll_math import money

SANCTION_CATEGORIES = {
    "verbal_notice",
    "first_warning",
    "final_warning",
    "salary_deduction",
    "suspension",
}
REWARD_CATEGORIES = {
    "financial_reward",
    "certificate",
    "commendation",
    "promotion_recommendation",
}


def _next_payroll_month(request_date: date) -> tuple[int, int]:
    if request_date.month == 12:
        return request_date.year + 1, 1
    return request_date.year, request_date.month + 1


def outstanding_advances_total(db: Session, employee_id: int) -> Decimal:
    rows = db.scalars(
        select(SalaryAdvance).where(
            SalaryAdvance.employee_id == employee_id,
            SalaryAdvance.status == "active",
        )
    ).all()
    return money(sum((row.amount for row in rows), Decimal("0")))


def create_salary_advance(
    db: Session,
    *,
    employee_id: int,
    request_date: date,
    amount: Decimal,
    reason: str,
    actor: str,
) -> SalaryAdvance:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("advance_reason_required")
    if amount is None or amount <= 0:
        raise ValueError("advance_amount_invalid")
    if not (15 <= request_date.day <= 22):
        raise ValueError("advance_outside_request_window")

    employee = db.get(Employee, employee_id)
    if employee is None or employee.status != "active":
        raise ValueError("advance_employee_not_active")

    outstanding = outstanding_advances_total(db, employee_id)
    limit = money(employee.basic_salary * Decimal("0.25"))
    projected = money(outstanding + amount)
    if projected > limit:
        raise ValueError("advance_exceeds_outstanding_limit")

    payroll_year, payroll_month = _next_payroll_month(request_date)
    advance = SalaryAdvance(
        employee_id=employee_id,
        request_date=request_date,
        request_day=request_date.day,
        amount=money(amount),
        payroll_year=payroll_year,
        payroll_month=payroll_month,
        status="active",
        reason=reason,
        created_by=actor,
    )
    db.add(advance)
    db.flush()
    db.add(AuditLog(
        actor=actor,
        action="salary_advance_created",
        entity_type="salary_advance",
        entity_id=str(advance.id),
        new_value=f"employee={employee_id}; amount={advance.amount}; payroll={payroll_year}-{payroll_month:02d}",
        reason=reason,
    ))
    db.flush()
    return advance


def cancel_salary_advance(db: Session, *, advance_id: int, reason: str, actor: str) -> SalaryAdvance:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("advance_cancel_reason_required")
    advance = db.scalar(select(SalaryAdvance).where(SalaryAdvance.id == advance_id).with_for_update())
    if advance is None:
        raise ValueError("advance_not_found")
    if advance.status != "active":
        raise ValueError("advance_not_cancellable")
    advance.status = "cancelled"
    advance.cancelled_by = actor
    advance.cancelled_at = datetime.utcnow()
    advance.cancel_reason = reason
    db.add(AuditLog(
        actor=actor,
        action="salary_advance_cancelled",
        entity_type="salary_advance",
        entity_id=str(advance.id),
        old_value="active",
        new_value="cancelled",
        reason=reason,
    ))
    db.flush()
    return advance


def create_staff_action(
    db: Session,
    *,
    employee_id: int,
    action_kind: str,
    category: str,
    action_date: date,
    amount: Decimal | None,
    reason: str,
    actor: str,
    payroll_year: int | None = None,
    payroll_month: int | None = None,
) -> StaffAction:
    reason = (reason or "").strip()
    category = (category or "").strip()
    if not reason:
        raise ValueError("staff_action_reason_required")
    if action_kind not in {"sanction", "reward"}:
        raise ValueError("staff_action_kind_invalid")
    allowed = SANCTION_CATEGORIES if action_kind == "sanction" else REWARD_CATEGORIES
    if category not in allowed:
        raise ValueError("staff_action_category_invalid")

    employee = db.get(Employee, employee_id)
    if employee is None:
        raise ValueError("staff_action_employee_not_found")

    if amount is not None and amount < 0:
        raise ValueError("staff_action_amount_invalid")
    if category in {"salary_deduction", "financial_reward"} and (amount is None or amount <= 0):
        raise ValueError("staff_action_amount_required")

    if amount and amount > 0:
        if payroll_year is None or payroll_month is None:
            payroll_year, payroll_month = action_date.year, action_date.month
        if not (1 <= payroll_month <= 12) or payroll_year < 2000:
            raise ValueError("staff_action_payroll_period_invalid")
    else:
        payroll_year = payroll_month = None
        amount = None

    action = StaffAction(
        employee_id=employee_id,
        action_kind=action_kind,
        category=category,
        action_date=action_date,
        amount=money(amount) if amount is not None else None,
        payroll_year=payroll_year,
        payroll_month=payroll_month,
        reason=reason,
        status="active",
        created_by=actor,
    )
    db.add(action)
    db.flush()
    db.add(AuditLog(
        actor=actor,
        action="staff_action_created",
        entity_type="staff_action",
        entity_id=str(action.id),
        new_value=f"{action_kind}:{category}; amount={action.amount}",
        reason=reason,
    ))
    db.flush()
    return action


def cancel_staff_action(db: Session, *, action_id: int, reason: str, actor: str) -> StaffAction:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("staff_action_cancel_reason_required")
    action = db.scalar(select(StaffAction).where(StaffAction.id == action_id).with_for_update())
    if action is None:
        raise ValueError("staff_action_not_found")
    if action.status != "active":
        raise ValueError("staff_action_not_cancellable")
    action.status = "cancelled"
    action.cancelled_by = actor
    action.cancelled_at = datetime.utcnow()
    action.cancel_reason = reason
    db.add(AuditLog(
        actor=actor,
        action="staff_action_cancelled",
        entity_type="staff_action",
        entity_id=str(action.id),
        old_value="active",
        new_value="cancelled",
        reason=reason,
    ))
    db.flush()
    return action
