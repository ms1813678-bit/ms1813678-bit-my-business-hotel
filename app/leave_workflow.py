"""Business rules for administrator-managed leave records and balance history."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    LeaveBalanceTransaction,
    LeaveRecord,
    LeaveType,
)


def leave_balance(db: Session, employee_id: int, leave_type_id: int) -> Decimal:
    value = db.scalar(
        select(func.coalesce(func.sum(LeaveBalanceTransaction.days_delta), 0)).where(
            LeaveBalanceTransaction.employee_id == employee_id,
            LeaveBalanceTransaction.leave_type_id == leave_type_id,
        )
    )
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def add_balance_entry(
    db: Session,
    *,
    employee_id: int,
    leave_type: LeaveType,
    transaction_type: str,
    days_delta: Decimal,
    reason: str,
    actor: str,
    leave_record_id: int | None = None,
) -> LeaveBalanceTransaction:
    if not leave_type.tracks_balance:
        raise ValueError("This leave type does not use a balance.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required for every balance transaction.")
    if days_delta == 0:
        raise ValueError("The balance change must be greater than zero or less than zero.")
    if transaction_type == "opening":
        if days_delta < 0:
            raise ValueError("An opening balance cannot be negative.")
        exists = db.scalar(
            select(LeaveBalanceTransaction.id).where(
                LeaveBalanceTransaction.employee_id == employee_id,
                LeaveBalanceTransaction.leave_type_id == leave_type.id,
                LeaveBalanceTransaction.transaction_type == "opening",
            ).limit(1)
        )
        if exists is not None:
            raise ValueError("An opening balance was already recorded; use an adjustment instead.")
    if transaction_type == "adjustment":
        current = leave_balance(db, employee_id, leave_type.id)
        if current + days_delta < 0:
            raise ValueError("The adjustment cannot make the balance negative.")
    entry = LeaveBalanceTransaction(
        employee_id=employee_id,
        leave_type_id=leave_type.id,
        leave_record_id=leave_record_id,
        transaction_type=transaction_type,
        days_delta=days_delta,
        reason=reason.strip(),
        created_by=actor,
    )
    db.add(entry)
    db.flush()
    db.add(AuditLog(
        actor=actor,
        action=f"leave_balance_{transaction_type}",
        entity_type="employee_leave_balance",
        entity_id=f"{employee_id}:{leave_type.id}",
        old_value=str(leave_balance(db, employee_id, leave_type.id) - days_delta),
        new_value=str(leave_balance(db, employee_id, leave_type.id)),
        reason=reason.strip(),
    ))
    return entry


def confirm_leave(db: Session, record: LeaveRecord, actor: str) -> None:
    if record.status != "draft":
        raise ValueError("Only a draft leave record can be confirmed.")
    leave_type = db.get(LeaveType, record.leave_type_id)
    if leave_type is None or not leave_type.active:
        raise ValueError("The selected leave type is not active.")
    if leave_type.tracks_balance:
        available = leave_balance(db, record.employee_id, leave_type.id)
        if available < record.charged_days:
            raise ValueError(
                f"Insufficient balance. Available: {available} days; "
                f"requested: {record.charged_days} days."
            )
        add_balance_entry(
            db,
            employee_id=record.employee_id,
            leave_type=leave_type,
            transaction_type="leave_use",
            days_delta=-Decimal(record.charged_days),
            reason=f"Confirmed leave record #{record.id}",
            actor=actor,
            leave_record_id=record.id,
        )
    record.status = "confirmed"
    record.confirmed_by = actor
    record.confirmed_at = datetime.utcnow()
    db.add(AuditLog(
        actor=actor,
        action="leave_confirmed",
        entity_type="leave_record",
        entity_id=str(record.id),
        old_value="draft",
        new_value="confirmed",
        reason=record.reason,
    ))


def cancel_leave(db: Session, record: LeaveRecord, actor: str, reason: str) -> None:
    if record.status == "cancelled":
        raise ValueError("This leave record is already cancelled.")
    if record.status == "confirmed":
        leave_type = db.get(LeaveType, record.leave_type_id)
        if leave_type and leave_type.tracks_balance:
            add_balance_entry(
                db,
                employee_id=record.employee_id,
                leave_type=leave_type,
                transaction_type="leave_reversal",
                days_delta=Decimal(record.charged_days),
                reason=f"Cancellation of leave record #{record.id}: {reason.strip()}",
                actor=actor,
                leave_record_id=record.id,
            )
    old_status = record.status
    record.status = "cancelled"
    record.cancelled_by = actor
    record.cancelled_at = datetime.utcnow()
    record.cancellation_reason = reason.strip()
    db.add(AuditLog(
        actor=actor,
        action="leave_cancelled",
        entity_type="leave_record",
        entity_id=str(record.id),
        old_value=old_status,
        new_value="cancelled",
        reason=reason.strip(),
    ))



def update_draft_leave(
    db: Session,
    record: LeaveRecord,
    *,
    start_date,
    end_date,
    charged_days: Decimal,
    reason: str,
    actor: str,
) -> None:
    """Edit a draft leave record and retain an immutable before/after audit entry."""
    if record.status != "draft":
        raise ValueError("Only draft leave records can be edited.")
    if end_date < start_date:
        raise ValueError("End date cannot be earlier than start date.")
    charged_days = Decimal(str(charged_days)).quantize(Decimal("0.01"))
    if charged_days <= 0:
        raise ValueError("Charged leave days must be positive.")
    calendar_days = (end_date - start_date).days + 1
    if charged_days > calendar_days:
        raise ValueError("Charged leave days cannot exceed calendar days in the leave period.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required.")

    overlap = db.scalar(
        select(LeaveRecord.id).where(
            LeaveRecord.employee_id == record.employee_id,
            LeaveRecord.id != record.id,
            LeaveRecord.status.in_(("draft", "confirmed")),
            LeaveRecord.start_date <= end_date,
            LeaveRecord.end_date >= start_date,
        ).limit(1)
    )
    if overlap is not None:
        raise ValueError("The edited leave dates overlap another active leave record.")

    old_value = (
        f"{record.start_date.isoformat()}|{record.end_date.isoformat()}|"
        f"{record.charged_days}|{record.reason}"
    )
    new_calendar_days = (end_date - start_date).days + 1
    record.start_date = start_date
    record.end_date = end_date
    record.calendar_days = new_calendar_days
    record.charged_days = charged_days
    record.reason = reason.strip()
    db.add(AuditLog(
        actor=actor,
        action="leave_draft_updated",
        entity_type="leave_record",
        entity_id=str(record.id),
        old_value=old_value,
        new_value=(
            f"{start_date.isoformat()}|{end_date.isoformat()}|"
            f"{charged_days}|{reason.strip()}"
        ),
        reason=f"Draft leave record edited by HR: {reason.strip()}",
    ))
