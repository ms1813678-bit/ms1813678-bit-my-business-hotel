"""Monthly payroll lifecycle with explicit attendance gate and audited edits."""
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog, AttendanceDailyRecord, AttendanceMonth, Employee, EmployeeServicePeriod,
    PayrollLine, PayrollPayment, PayrollPeriod,
)
from app.payroll_math import absence_deduction, lateness_deduction, money, prorated_new_hire_salary


def _month_bounds(year: int, month: int):
    if year < 2000 or not 1 <= month <= 12:
        raise ValueError("payroll_period_invalid")
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def _recalculate(line: PayrollLine, period: PayrollPeriod):
    line.base_pay = money(line.base_salary)
    line.overtime_pay = money(
        line.base_salary / (line.daily_hours * Decimal(30)) * line.overtime_hours
    )
    line.absence_deduction = absence_deduction(
        line.base_salary, period.year, period.month, int(line.absence_days)
    )
    line.lateness_deduction = lateness_deduction(
        line.base_salary, line.daily_hours, line.late_minutes, line.early_minutes
    )
    line.gross_pay = money(
        line.base_pay + line.attendance_bonus + line.performance_bonus
        + line.occupancy_bonus + line.overtime_pay + line.other_bonuses
    )
    deductions = (
        line.absence_deduction + line.lateness_deduction + line.insurance_share
        + line.tax_withheld + line.other_deductions + line.advance_deduction
    )
    line.net_pay = money(max(Decimal("0"), line.gross_pay - deductions))
    line.updated_at = datetime.utcnow()


def create_payroll_period(db: Session, *, year: int, month: int, actor: str) -> PayrollPeriod:
    start, end = _month_bounds(year, month)
    attendance = db.scalar(select(AttendanceMonth).where(
        AttendanceMonth.period_start == start, AttendanceMonth.period_end == end
    ).with_for_update())
    if attendance is None or attendance.status != "approved":
        raise ValueError("attendance_month_not_approved")
    existing = db.scalar(select(PayrollPeriod).where(
        PayrollPeriod.year == year, PayrollPeriod.month == month
    ))
    if existing:
        raise ValueError("payroll_period_already_exists")
    period = PayrollPeriod(
        year=year, month=month, attendance_month_id=attendance.id,
        status="draft", created_by=actor,
    )
    db.add(period)
    db.flush()
    employees = db.scalars(select(Employee).join(
        EmployeeServicePeriod, EmployeeServicePeriod.employee_id == Employee.id
    ).where(
        EmployeeServicePeriod.start_date <= end,
        (EmployeeServicePeriod.end_date.is_(None)) | (EmployeeServicePeriod.end_date >= start),
    ).order_by(Employee.code)).all()
    for employee in employees:
        service = db.scalar(select(EmployeeServicePeriod).where(
            EmployeeServicePeriod.employee_id == employee.id,
            EmployeeServicePeriod.start_date <= end,
            (EmployeeServicePeriod.end_date.is_(None)) | (EmployeeServicePeriod.end_date >= start),
        ).order_by(EmployeeServicePeriod.start_date.desc()).limit(1))
        effective_start = max(start, service.start_date)
        base = employee.basic_salary
        if effective_start > start:
            days = (end - effective_start).days + 1
            base = money(employee.basic_salary * Decimal(days) / Decimal((end-start).days + 1))
        row = PayrollLine(
            payroll_period_id=period.id, employee_id=employee.id,
            employee_code=employee.code, employee_name=employee.full_name,
            base_salary=base, daily_hours=employee.daily_hours,
            attendance_bonus=0, performance_bonus=0, occupancy_bonus=0,
            overtime_hours=0, other_bonuses=0, absence_days=0, late_minutes=0, early_minutes=0,
            insurance_share=0, tax_withheld=0, other_deductions=0, advance_deduction=0,
            base_pay=base, overtime_pay=0, absence_deduction=0, lateness_deduction=0,
            gross_pay=base, net_pay=base, payment_status="unpaid",
        )
        db.add(row)
        # Only approved punches with an explicit scheduled boundary contribute to minutes.
        records = db.scalars(select(AttendanceDailyRecord).where(
            AttendanceDailyRecord.employee_id == employee.id,
            AttendanceDailyRecord.work_date >= effective_start,
            AttendanceDailyRecord.work_date <= end,
            AttendanceDailyRecord.status == "approved",
        )).all()
        late = early = 0
        for rec in records:
            if rec.approved_clock_in is not None and rec.scheduled_start_at is not None:
                late += max(0, int((rec.approved_clock_in - rec.scheduled_start_at).total_seconds() // 60))
            if rec.approved_clock_out is not None and rec.scheduled_end_at is not None:
                early += max(0, int((rec.scheduled_end_at - rec.approved_clock_out).total_seconds() // 60))
        row.late_minutes, row.early_minutes = late, early
        _recalculate(row, period)
    db.add(AuditLog(actor=actor, action="payroll_created", entity_type="payroll_period",
                    entity_id=str(period.id), new_value=f"{year}-{month:02d}",
                    reason="Created from approved attendance; absence days require explicit HR entry."))
    db.flush()
    return period


def update_payroll_line(db: Session, *, line_id: int, values: dict, reason: str, actor: str):
    line = db.scalar(select(PayrollLine).where(PayrollLine.id == line_id).with_for_update())
    if line is None:
        raise ValueError("payroll_line_not_found")
    period = db.scalar(select(PayrollPeriod).where(PayrollPeriod.id == line.payroll_period_id).with_for_update())
    if period.status not in ("draft", "reopened"):
        raise ValueError("payroll_period_locked")
    reason = reason.strip()
    if not reason:
        raise ValueError("payroll_change_reason_required")
    allowed = {
        "attendance_bonus", "performance_bonus", "occupancy_bonus", "overtime_hours",
        "other_bonuses", "absence_days", "late_minutes", "early_minutes", "insurance_share",
        "tax_withheld", "other_deductions", "advance_deduction", "review_notes",
    }
    if not values or not set(values).issubset(allowed):
        raise ValueError("payroll_fields_invalid")
    old = {k: str(getattr(line, k)) for k in values}
    for key, value in values.items():
        setattr(line, key, value)
    if line.absence_days != line.absence_days.to_integral_value():
        raise ValueError("payroll_absence_days_must_be_whole")
    _recalculate(line, period)
    db.add(AuditLog(actor=actor, action="payroll_line_updated", entity_type="payroll_line",
                    entity_id=str(line.id), old_value=str(old),
                    new_value=str({k: str(getattr(line, k)) for k in values}), reason=reason))
    db.flush()
    return line


def approve_payroll(db: Session, *, period_id: int, reason: str, actor: str):
    period = db.scalar(select(PayrollPeriod).where(PayrollPeriod.id == period_id).with_for_update())
    if period is None:
        raise ValueError("payroll_period_not_found")
    if period.status not in ("draft", "reopened"):
        raise ValueError("payroll_period_not_approvable")
    reason = reason.strip()
    if not reason:
        raise ValueError("payroll_approval_reason_required")
    period.status, period.approved_by, period.approved_at, period.approval_reason = "approved", actor, datetime.utcnow(), reason
    db.add(AuditLog(actor=actor, action="payroll_approved", entity_type="payroll_period",
                    entity_id=str(period.id), old_value="draft", new_value="approved", reason=reason))
    db.flush()
    return period


def close_payroll(db: Session, *, period_id: int, actor: str):
    period = db.scalar(select(PayrollPeriod).where(PayrollPeriod.id == period_id).with_for_update())
    if period is None or period.status != "approved":
        raise ValueError("payroll_period_not_approved")
    period.status, period.closed_by, period.closed_at = "closed", actor, datetime.utcnow()
    db.add(AuditLog(actor=actor, action="payroll_closed", entity_type="payroll_period",
                    entity_id=str(period.id), old_value="approved", new_value="closed", reason="Payroll closed"))
    db.flush()
    return period


def reopen_payroll(db: Session, *, period_id: int, reason: str, actor: str):
    period = db.scalar(select(PayrollPeriod).where(PayrollPeriod.id == period_id).with_for_update())
    reason = reason.strip()
    if period is None or period.status != "closed":
        raise ValueError("payroll_period_not_closed")
    if not reason:
        raise ValueError("payroll_reopen_reason_required")
    period.status, period.reopened_by, period.reopened_at, period.reopen_reason = "reopened", actor, datetime.utcnow(), reason
    db.add(AuditLog(actor=actor, action="payroll_reopened", entity_type="payroll_period",
                    entity_id=str(period.id), old_value="closed", new_value="reopened", reason=reason))
    db.flush()
    return period


def record_payment(db: Session, *, line_id: int, amount: Decimal, paid_on: date, method: str,
                   reference: str, actor: str):
    line = db.scalar(select(PayrollLine).where(PayrollLine.id == line_id).with_for_update())
    if line is None:
        raise ValueError("payroll_line_not_found")
    period = db.get(PayrollPeriod, line.payroll_period_id)
    if period.status not in ("approved", "closed"):
        raise ValueError("payroll_not_approved")
    if amount <= 0 or method not in ("cash", "bank_transfer"):
        raise ValueError("payroll_payment_invalid")
    paid = sum(db.scalars(select(PayrollPayment.amount).where(PayrollPayment.payroll_line_id == line.id)).all(), Decimal(0))
    if paid + amount > line.net_pay:
        raise ValueError("payroll_payment_exceeds_net")
    payment = PayrollPayment(payroll_line_id=line.id, amount=amount, paid_on=paid_on, method=method,
                             reference=reference.strip() or None, created_by=actor)
    db.add(payment)
    total = paid + amount
    line.payment_status = "paid" if total == line.net_pay else "partial"
    db.add(AuditLog(actor=actor, action="payroll_payment_recorded", entity_type="payroll_line",
                    entity_id=str(line.id), old_value=str(paid), new_value=str(total),
                    reason=f"{method} payment {paid_on.isoformat()}"))
    db.flush()
    return payment
