"""Administrator-controlled employee advances and repayment scheduling."""
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AdvanceRequest, AuditLog, Employee


ADVANCE_RATE = Decimal("0.25")
ACTIVE_CAP_STATUSES = ("pending", "approved", "disbursed", "deducted")


def first_of_next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def monthly_advance_total(db: Session, employee_id: int, salary_month: date) -> Decimal:
    total = db.scalar(
        select(func.coalesce(func.sum(AdvanceRequest.amount), 0)).where(
            AdvanceRequest.employee_id == employee_id,
            AdvanceRequest.salary_month == salary_month,
            AdvanceRequest.status.in_(ACTIVE_CAP_STATUSES),
        )
    )
    return Decimal(str(total or 0)).quantize(Decimal("0.01"))


def create_advance_request(
    db: Session,
    *,
    employee: Employee,
    amount: Decimal,
    request_date: date,
    reason: str,
    actor: str,
) -> AdvanceRequest:
    if employee.status != "active":
        raise ValueError("Advances can only be requested for active employees.")
    if request_date.day < 15 or request_date.day > 22:
        raise ValueError("Advance requests are accepted from day 15 through day 22.")
    if amount <= 0:
        raise ValueError("Advance amount must be greater than zero.")
    if not reason.strip():
        raise ValueError("A reason is required.")
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    salary_month = request_date.replace(day=1)
    cap = (Decimal(employee.basic_salary) * ADVANCE_RATE).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    already_committed = monthly_advance_total(db, employee.id, salary_month)
    if already_committed + amount > cap:
        remaining = max(Decimal("0.00"), cap - already_committed)
        raise ValueError(
            f"Monthly advance limit is {cap}; already committed {already_committed}; "
            f"remaining {remaining}."
        )
    item = AdvanceRequest(
        employee_id=employee.id,
        request_date=request_date,
        salary_month=salary_month,
        repayment_month=first_of_next_month(salary_month),
        amount=amount,
        reason=reason.strip(),
        status="pending",
        created_by=actor,
    )
    db.add(item)
    db.flush()
    db.add(AuditLog(
        actor=actor,
        action="advance_requested",
        entity_type="advance_request",
        entity_id=str(item.id),
        new_value=f"{amount}|{item.salary_month}|{item.repayment_month}",
        reason=reason.strip(),
    ))
    return item


def approve_advance(db: Session, item: AdvanceRequest, actor: str, reason: str) -> None:
    if item.status != "pending":
        raise ValueError("Only pending advance requests can be approved.")
    employee = db.get(Employee, item.employee_id)
    if employee is None or employee.status != "active":
        raise ValueError("The employee is no longer active.")
    cap = (Decimal(employee.basic_salary) * ADVANCE_RATE).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    committed = monthly_advance_total(db, employee.id, item.salary_month)
    if committed > cap:
        raise ValueError("The employee salary changed and the monthly advance limit is exceeded.")
    item.status = "approved"
    item.decided_by = actor
    item.decided_at = datetime.utcnow()
    item.decision_reason = reason.strip()
    db.add(AuditLog(
        actor=actor,
        action="advance_approved",
        entity_type="advance_request",
        entity_id=str(item.id),
        old_value="pending",
        new_value="approved",
        reason=reason.strip() or "Approved by HR administrator",
    ))


def reject_advance(db: Session, item: AdvanceRequest, actor: str, reason: str) -> None:
    if item.status != "pending":
        raise ValueError("Only pending advance requests can be rejected.")
    if not reason.strip():
        raise ValueError("A rejection reason is required.")
    item.status = "rejected"
    item.decided_by = actor
    item.decided_at = datetime.utcnow()
    item.decision_reason = reason.strip()
    db.add(AuditLog(
        actor=actor,
        action="advance_rejected",
        entity_type="advance_request",
        entity_id=str(item.id),
        old_value="pending",
        new_value="rejected",
        reason=reason.strip(),
    ))


def disburse_advance(db: Session, item: AdvanceRequest, actor: str, reason: str) -> None:
    if item.status != "approved":
        raise ValueError("Only approved advances can be marked as disbursed.")
    if not reason.strip():
        raise ValueError("A disbursement reference or reason is required.")
    item.status = "disbursed"
    item.disbursed_by = actor
    item.disbursed_at = datetime.utcnow()
    db.add(AuditLog(
        actor=actor,
        action="advance_disbursed",
        entity_type="advance_request",
        entity_id=str(item.id),
        old_value="approved",
        new_value="disbursed",
        reason=reason.strip(),
    ))


def cancel_advance(db: Session, item: AdvanceRequest, actor: str, reason: str) -> None:
    if item.status not in {"pending", "approved"}:
        raise ValueError("Only pending or approved advances can be cancelled before payment.")
    if not reason.strip():
        raise ValueError("A cancellation reason is required.")
    old_status = item.status
    item.status = "cancelled"
    item.decided_by = actor
    item.decided_at = datetime.utcnow()
    item.decision_reason = reason.strip()
    db.add(AuditLog(
        actor=actor,
        action="advance_cancelled",
        entity_type="advance_request",
        entity_id=str(item.id),
        old_value=old_status,
        new_value="cancelled",
        reason=reason.strip(),
    ))
