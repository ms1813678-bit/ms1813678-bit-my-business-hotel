"""Administrator-only leave type, manual balance, and confirmed leave operations."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    Employee,
    EmployeeLeaveBalance,
    LeaveBalanceTransaction,
    LeaveRecord,
    LeaveType,
)


def create_leave_type(
    db: Session, *, code: str, name: str, paid: bool, deducts_balance: bool,
) -> LeaveType:
    clean_code, clean_name = code.strip().lower(), name.strip()
    if not clean_code or not clean_name:
        raise ValueError("leave_type_code_and_name_required")
    if deducts_balance and not paid:
        # Unpaid leave has no paid entitlement balance to consume.
        deducts_balance = False
    leave_type = LeaveType(
        code=clean_code, name=clean_name, paid=paid, deducts_balance=deducts_balance, active=True,
    )
    db.add(leave_type)
    db.flush()
    return leave_type


def set_manual_balance(
    db: Session, *, employee_id: int, leave_type_id: int, balance: Decimal,
    reason: str, actor: str,
) -> EmployeeLeaveBalance:
    reason = reason.strip()
    if not reason:
        raise ValueError("leave_balance_reason_required")
    if balance < 0:
        raise ValueError("leave_balance_must_be_nonnegative")
    employee = db.get(Employee, employee_id)
    leave_type = db.get(LeaveType, leave_type_id)
    if employee is None or leave_type is None or not leave_type.active:
        raise ValueError("employee_or_leave_type_unavailable")

    row = db.scalar(
        select(EmployeeLeaveBalance)
        .where(
            EmployeeLeaveBalance.employee_id == employee_id,
            EmployeeLeaveBalance.leave_type_id == leave_type_id,
        )
        .with_for_update()
    )
    old_balance = row.current_balance if row is not None else Decimal("0")
    if row is None:
        row = EmployeeLeaveBalance(
            employee_id=employee_id, leave_type_id=leave_type_id,
            opening_balance=balance, current_balance=balance, updated_at=datetime.utcnow(),
        )
        db.add(row)
    else:
        row.current_balance = balance
        row.updated_at = datetime.utcnow()

    delta = balance - old_balance
    db.add(LeaveBalanceTransaction(
        employee_id=employee_id, leave_type_id=leave_type_id, leave_record_id=None,
        change_amount=delta, balance_after=balance, action="manual_adjustment",
        reason=reason, actor=actor,
    ))
    db.add(AuditLog(
        actor=actor, action="leave_balance_adjusted", entity_type="employee",
        entity_id=str(employee_id), old_value=str(old_balance), new_value=str(balance),
        reason=reason,
    ))
    db.flush()
    return row


def confirm_leave(
    db: Session, *, employee_id: int, leave_type_id: int, start_date: date,
    end_date: date, days: Decimal, reason: str, actor: str,
) -> LeaveRecord:
    reason = reason.strip()
    if not reason:
        raise ValueError("leave_reason_required")
    if end_date < start_date or days <= 0 or days > Decimal((end_date - start_date).days + 1):
        raise ValueError("leave_dates_or_days_invalid")

    employee = db.scalar(select(Employee).where(Employee.id == employee_id).with_for_update())
    leave_type = db.get(LeaveType, leave_type_id)
    if employee is None or employee.status != "active" or leave_type is None or not leave_type.active:
        raise ValueError("employee_or_leave_type_unavailable")
    if start_date < employee.hire_date:
        raise ValueError("leave_before_employment")

    overlap = db.scalar(
        select(LeaveRecord.id)
        .where(
            LeaveRecord.employee_id == employee_id,
            LeaveRecord.status == "confirmed",
            LeaveRecord.start_date <= end_date,
            LeaveRecord.end_date >= start_date,
        )
        .limit(1)
    )
    if overlap is not None:
        raise ValueError("leave_dates_overlap_existing_record")

    balance = None
    if leave_type.deducts_balance:
        balance = db.scalar(
            select(EmployeeLeaveBalance)
            .where(
                EmployeeLeaveBalance.employee_id == employee_id,
                EmployeeLeaveBalance.leave_type_id == leave_type_id,
            )
            .with_for_update()
        )
        if balance is None or balance.current_balance < days:
            raise ValueError("leave_balance_insufficient")

    record = LeaveRecord(
        employee_id=employee_id, leave_type_id=leave_type_id,
        start_date=start_date, end_date=end_date, days=days, status="confirmed",
        reason=reason, created_by=actor, created_at=datetime.utcnow(),
    )
    db.add(record)
    db.flush()

    if balance is not None:
        balance.current_balance -= days
        balance.updated_at = datetime.utcnow()
        db.add(LeaveBalanceTransaction(
            employee_id=employee_id, leave_type_id=leave_type_id, leave_record_id=record.id,
            change_amount=-days, balance_after=balance.current_balance,
            action="leave_confirmed", reason=reason, actor=actor,
        ))
    db.add(AuditLog(
        actor=actor, action="leave_confirmed", entity_type="leave_record",
        entity_id=str(record.id), new_value=f"{start_date.isoformat()}..{end_date.isoformat()} ({days} days)",
        reason=reason,
    ))
    db.flush()
    return record


def cancel_leave(db: Session, *, leave_record_id: int, reason: str, actor: str) -> LeaveRecord:
    reason = reason.strip()
    if not reason:
        raise ValueError("leave_cancellation_reason_required")
    record = db.scalar(
        select(LeaveRecord).where(LeaveRecord.id == leave_record_id).with_for_update()
    )
    if record is None:
        raise ValueError("leave_record_not_found")
    if record.status != "confirmed":
        raise ValueError("leave_record_not_confirmed")
    leave_type = db.get(LeaveType, record.leave_type_id)
    record.status = "cancelled"

    if leave_type is not None and leave_type.deducts_balance:
        balance = db.scalar(
            select(EmployeeLeaveBalance)
            .where(
                EmployeeLeaveBalance.employee_id == record.employee_id,
                EmployeeLeaveBalance.leave_type_id == record.leave_type_id,
            )
            .with_for_update()
        )
        if balance is None:
            raise ValueError("leave_balance_missing_for_cancellation")
        balance.current_balance += record.days
        balance.updated_at = datetime.utcnow()
        db.add(LeaveBalanceTransaction(
            employee_id=record.employee_id, leave_type_id=record.leave_type_id,
            leave_record_id=record.id, change_amount=record.days,
            balance_after=balance.current_balance, action="leave_cancelled",
            reason=reason, actor=actor,
        ))

    db.add(AuditLog(
        actor=actor, action="leave_cancelled", entity_type="leave_record",
        entity_id=str(record.id), old_value="confirmed", new_value="cancelled", reason=reason,
    ))
    db.flush()
    return record
