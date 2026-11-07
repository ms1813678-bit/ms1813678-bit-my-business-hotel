"""Salary advance workflow with monthly caps and next-payroll tracking."""
from datetime import datetime, date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AdvanceRequest, AuditLog, Employee


RESERVING_STATUSES = ("pending", "approved", "paid")


def payroll_month_for_advance(request_date: date) -> date:
    """Return the salary period the advance is charged against.

    Payroll for this month is paid on the 5th of the following month.
    Example: a 15 September advance belongs to September payroll, paid 5 October.
    """
    return date(request_date.year, request_date.month, 1)


def next_payroll_month(request_date: date) -> date:
    """Legacy helper retained for callers; not used to assign an advance."""
    if request_date.month == 12:
        return date(request_date.year + 1, 1, 1)
    return date(request_date.year, request_date.month + 1, 1)


def outstanding_amount(db: Session, employee_id: int, exclude_id: int | None = None) -> Decimal:
    statement = select(func.coalesce(func.sum(AdvanceRequest.amount), 0)).where(
        AdvanceRequest.employee_id == employee_id,
        AdvanceRequest.status.in_(RESERVING_STATUSES),
    )
    if exclude_id is not None:
        statement = statement.where(AdvanceRequest.id != exclude_id)
    value = db.scalar(statement)
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _audit(db: Session, *, actor: str, action: str, request: AdvanceRequest, old: str, new: str, reason: str) -> None:
    db.add(AuditLog(
        actor=actor,
        action=action,
        entity_type="advance_request",
        entity_id=str(request.id),
        old_value=old,
        new_value=new,
        reason=reason,
    ))


def create_advance(
    db: Session,
    *,
    employee_id: int,
    request_date: date,
    amount: Decimal,
    reason: str,
    actor: str,
) -> AdvanceRequest:
    if request_date.day < 15 or request_date.day > 22:
        raise ValueError("Advance requests are allowed only from day 15 through day 22.")
    if request_date > date.today():
        raise ValueError("An advance request date cannot be in the future.")
    if amount <= 0 or not reason.strip():
        raise ValueError("Enter a positive amount and a reason.")

    employee = db.scalar(
        select(Employee).where(Employee.id == employee_id).with_for_update()
    )
    if employee is None or employee.status != "active":
        raise ValueError("The employee is not active.")
    salary = Decimal(str(employee.basic_salary))
    if salary <= 0:
        raise ValueError("The employee must have a positive basic salary.")

    payroll_month = payroll_month_for_advance(request_date)
    cap = (salary * Decimal("0.25")).quantize(Decimal("0.01"))
    already_outstanding = outstanding_amount(db, employee_id)
    if already_outstanding + amount > cap:
        raise ValueError(
            f"Outstanding advance limit is {cap}; already outstanding: {already_outstanding}."
        )

    request = AdvanceRequest(
        employee_id=employee_id,
        request_date=request_date,
        payroll_month=payroll_month,
        amount=amount,
        salary_basis=salary,
        reason=reason.strip(),
        status="pending",
        created_by=actor,
    )
    db.add(request)
    db.flush()
    _audit(
        db, actor=actor, action="advance_requested", request=request,
        old="", new=f"{amount}|{payroll_month.isoformat()}",
        reason=reason.strip(),
    )
    return request


def approve_advance(db: Session, request: AdvanceRequest, actor: str) -> None:
    if request.status != "pending":
        raise ValueError("Only a pending advance can be approved.")
    employee = db.scalar(
        select(Employee).where(Employee.id == request.employee_id).with_for_update()
    )
    if employee is None or employee.status != "active":
        raise ValueError("The employee is not active.")
    cap = (Decimal(request.salary_basis) * Decimal("0.25")).quantize(Decimal("0.01"))
    already_outstanding = outstanding_amount(
        db, request.employee_id, exclude_id=request.id
    )
    if already_outstanding + Decimal(request.amount) > cap:
        raise ValueError("The outstanding advance limit would be exceeded.")
    request.status = "approved"
    request.approved_by = actor
    request.approved_at = datetime.utcnow()
    _audit(
        db, actor=actor, action="advance_approved", request=request,
        old="pending", new="approved", reason=request.reason,
    )


def reject_advance(db: Session, request: AdvanceRequest, actor: str, reason: str) -> None:
    if request.status != "pending":
        raise ValueError("Only a pending advance can be rejected.")
    if not reason.strip():
        raise ValueError("A rejection reason is required.")
    request.status = "rejected"
    request.rejected_by = actor
    request.rejected_at = datetime.utcnow()
    request.rejection_reason = reason.strip()
    _audit(
        db, actor=actor, action="advance_rejected", request=request,
        old="pending", new="rejected", reason=reason.strip(),
    )


def mark_advance_paid(db: Session, request: AdvanceRequest, actor: str) -> None:
    if request.status != "approved":
        raise ValueError("Only an approved advance can be marked as paid.")
    request.status = "paid"
    request.paid_by = actor
    request.paid_at = datetime.utcnow()
    _audit(
        db, actor=actor, action="advance_paid", request=request,
        old="approved", new="paid", reason="Payment confirmed by HR administrator.",
    )
