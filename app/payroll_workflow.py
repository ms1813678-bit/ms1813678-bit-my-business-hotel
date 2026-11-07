"""Payroll period lifecycle and payment ledger.

This foundation deliberately does not calculate attendance/hourly deductions.
Those formulas remain blocked until the hotel confirms the 30-versus-40 divisor.
"""

from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AttendanceMonth,
    AuditLog,
    Employee,
    EmployeeServicePeriod,
    PayrollLine,
    PayrollPayment,
    PayrollPeriod,
)


ZERO = Decimal("0.00")
EDITABLE_AMOUNT_FIELDS = {
    "attendance_bonus", "performance_bonus", "occupancy_bonus",
    "overtime_amount", "other_bonus", "absence_deduction",
    "lateness_deduction", "insurance_share", "taxes",
    "other_deductions", "advance_deduction",
}


def month_bounds(period_start: date) -> tuple[date, date]:
    if period_start.day != 1:
        raise ValueError("Payroll period must start on the first day of a month.")
    return period_start, date(
        period_start.year, period_start.month,
        monthrange(period_start.year, period_start.month)[1],
    )


def _audit(db: Session, actor: str, action: str, entity_type: str,
           entity_id: str, old_value: str, new_value: str, reason: str) -> None:
    db.add(AuditLog(
        actor=actor, action=action, entity_type=entity_type,
        entity_id=entity_id, old_value=old_value,
        new_value=new_value, reason=reason,
    ))


def create_payroll_period(db: Session, period_start: date, actor: str) -> PayrollPeriod:
    start, end = month_bounds(period_start)
    if db.scalar(select(PayrollPeriod.id).where(PayrollPeriod.period_start == start)):
        raise ValueError("A payroll period already exists for this month.")

    service_periods = db.scalars(
        select(EmployeeServicePeriod).where(
            EmployeeServicePeriod.start_date <= end,
            (EmployeeServicePeriod.end_date.is_(None))
            | (EmployeeServicePeriod.end_date >= start),
        )
    ).all()
    service_days_by_employee: dict[int, int] = {}
    for item in service_periods:
        overlap_start = max(item.start_date, start)
        overlap_end = min(item.end_date or end, end)
        if overlap_start <= overlap_end:
            service_days_by_employee[item.employee_id] = (
                service_days_by_employee.get(item.employee_id, 0)
                + (overlap_end - overlap_start).days + 1
            )
    employee_ids = set(service_days_by_employee)

    # Legacy active employees without a service-period row remain eligible.
    legacy_active = db.scalars(
        select(Employee).where(
            Employee.status == "active",
            Employee.hire_date <= end,
        )
    ).all()
    for item in legacy_active:
        if item.id not in service_days_by_employee:
            overlap_start = max(item.hire_date, start)
            if overlap_start <= end:
                service_days_by_employee[item.id] = (end - overlap_start).days + 1
                employee_ids.add(item.id)

    if not employee_ids:
        raise ValueError("No employees have a service period in this payroll month.")

    next_year, next_month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    period = PayrollPeriod(
        period_start=start,
        period_end=end,
        payment_due_date=date(next_year, next_month, 5),
        status="draft",
        created_by=actor,
    )
    db.add(period)
    db.flush()

    for employee_id in sorted(employee_ids):
        employee = db.get(Employee, employee_id)
        if employee is None:
            continue
        line = PayrollLine(
            payroll_period_id=period.id,
            employee_id=employee.id,
            employee_code_snapshot=employee.code,
            employee_name_snapshot=employee.full_name,
            department_name_snapshot=employee.department.name if employee.department else "",
            position_snapshot=employee.position,
            basic_salary_snapshot=(
                Decimal(employee.basic_salary) * Decimal(service_days_by_employee[employee.id])
                / Decimal((end - start).days + 1)
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            monthly_basic_salary_snapshot=Decimal(employee.basic_salary),
            payable_service_days=min(service_days_by_employee[employee.id], (end - start).days + 1),
            daily_hours_snapshot=Decimal(employee.daily_hours),
        )
        db.add(line)

    db.flush()
    _audit(
        db, actor, "payroll_period_created", "payroll_period", str(period.id),
        "", f"{start.isoformat()}|{end.isoformat()}",
        "Monthly payroll period and employee snapshots created.",
    )
    return period


def update_payroll_line(
    db: Session, line: PayrollLine, *, actor: str,
    amounts: dict[str, Decimal], notes: str | None = None,
) -> None:
    period = db.get(PayrollPeriod, line.payroll_period_id)
    if period is None or period.status != "draft":
        raise ValueError("Only draft payroll lines can be edited.")
    if not amounts or set(amounts) - EDITABLE_AMOUNT_FIELDS:
        raise ValueError("One or more payroll amount fields are not editable.")
    changes = []
    for field, raw_value in amounts.items():
        value = Decimal(str(raw_value)).quantize(Decimal("0.01"))
        if not value.is_finite() or value < ZERO:
            raise ValueError("Payroll amounts must be finite and non-negative.")
        old = Decimal(getattr(line, field) or 0)
        if old != value:
            setattr(line, field, value)
            changes.append(f"{field}:{old}->{value}")
    if notes is not None:
        old_note = line.notes or ""
        line.notes = notes.strip() or None
        if old_note != (line.notes or ""):
            changes.append("notes updated")
    if changes:
        _audit(
            db, actor, "payroll_line_updated", "payroll_line", str(line.id),
            "", ";".join(changes), "HR administrator updated draft payroll values.",
        )


def approve_payroll_period(db: Session, period: PayrollPeriod, actor: str) -> None:
    if period.status != "draft":
        raise ValueError("Only a draft payroll period can be approved.")
    attendance = db.scalar(
        select(AttendanceMonth).where(
            AttendanceMonth.period_start == period.period_start,
            AttendanceMonth.period_end == period.period_end,
        )
    )
    if attendance is None or attendance.status != "approved":
        raise ValueError("Attendance for this exact month must be approved first.")
    lines = db.scalars(
        select(PayrollLine).where(PayrollLine.payroll_period_id == period.id)
    ).all()
    if not lines:
        raise ValueError("Payroll period has no employee lines.")
    if any(line.net_pay < ZERO for line in lines):
        raise ValueError("A payroll line has a negative net amount and needs review.")
    period.status = "approved"
    period.approved_by = actor
    period.approved_at = datetime.utcnow()
    _audit(
        db, actor, "payroll_period_approved", "payroll_period", str(period.id),
        "draft", "approved", "HR approved payroll after attendance approval.",
    )


def close_payroll_period(db: Session, period: PayrollPeriod, actor: str) -> None:
    if period.status != "approved":
        raise ValueError("Only an approved payroll period can be closed.")
    period.status = "closed"
    period.closed_by = actor
    period.closed_at = datetime.utcnow()
    _audit(
        db, actor, "payroll_period_closed", "payroll_period", str(period.id),
        "approved", "closed", "Payroll period closed by HR administrator.",
    )


def reopen_payroll_period(
    db: Session, period: PayrollPeriod, actor: str, reason: str
) -> None:
    if period.status != "closed":
        raise ValueError("Only a closed payroll period can be reopened.")
    if not reason.strip():
        raise ValueError("A reason is required to reopen payroll.")
    period.status = "draft"
    period.reopened_by = actor
    period.reopened_at = datetime.utcnow()
    period.reopen_reason = reason.strip()
    _audit(
        db, actor, "payroll_period_reopened", "payroll_period", str(period.id),
        "closed", "draft", reason.strip(),
    )


def payment_totals(db: Session, line_id: int) -> tuple[Decimal, Decimal]:
    paid = db.scalar(
        select(func.coalesce(func.sum(PayrollPayment.amount), 0)).where(
            PayrollPayment.payroll_line_id == line_id
        )
    )
    total_paid = Decimal(str(paid or 0)).quantize(Decimal("0.01"))
    line = db.get(PayrollLine, line_id)
    if line is None:
        raise ValueError("Payroll line was not found.")
    return total_paid, max(ZERO, line.net_pay - total_paid)


def record_payroll_payment(
    db: Session, line: PayrollLine, *, amount: Decimal,
    payment_date: date, payment_method: str, actor: str,
    reference: str | None = None,
) -> PayrollPayment:
    period = db.get(PayrollPeriod, line.payroll_period_id)
    if period is None or period.status not in {"approved", "closed"}:
        raise ValueError("Payments can be recorded only for approved payroll.")
    value = Decimal(str(amount)).quantize(Decimal("0.01"))
    if not value.is_finite() or value <= ZERO:
        raise ValueError("Payment amount must be positive.")
    if payment_method not in {"cash", "bank_transfer"}:
        raise ValueError("Unsupported payment method.")
    paid, outstanding = payment_totals(db, line.id)
    if value > outstanding:
        raise ValueError("Payment exceeds the remaining net salary.")
    payment = PayrollPayment(
        payroll_line_id=line.id, amount=value, payment_date=payment_date,
        payment_method=payment_method, reference=(reference or "").strip() or None,
        recorded_by=actor,
    )
    db.add(payment)
    db.flush()
    _audit(
        db, actor, "payroll_payment_recorded", "payroll_line", str(line.id),
        str(paid), str(paid + value),
        f"{payment_method}|{payment_date.isoformat()}",
    )
    return payment
