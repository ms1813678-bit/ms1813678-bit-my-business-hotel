from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.leave_service import (
    cancel_leave,
    confirm_leave,
    create_leave_type,
    set_manual_balance,
)
from app.models import (
    AuditLog,
    Department,
    Employee,
    EmployeeLeaveBalance,
    LeaveBalanceTransaction,
    LeaveRecord,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        department = Department(name="Housekeeping", active=True)
        db.add(department)
        db.flush()
        employee = Employee(
            code="MY-0001", full_name="Test Employee", department_id=department.id,
            position="Housekeeper", hire_date=date(2026, 1, 1),
            basic_salary=Decimal("5000.00"), daily_hours=Decimal("8"), status="active",
        )
        db.add(employee)
        db.commit()
        yield db, employee.id
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_confirm_leave_deducts_manual_balance_and_cancel_restores_it(db_session):
    db, employee_id = db_session
    leave_type = create_leave_type(
        db, code="annual", name="Annual leave", paid=True, deducts_balance=True,
    )
    db.flush()
    balance = set_manual_balance(
        db, employee_id=employee_id, leave_type_id=leave_type.id,
        balance=Decimal("5"), reason="Opening balance entered from HR records", actor="admin",
    )
    record = confirm_leave(
        db, employee_id=employee_id, leave_type_id=leave_type.id,
        start_date=date(2026, 3, 10), end_date=date(2026, 3, 12),
        days=Decimal("3"), reason="Planned annual leave", actor="admin",
    )
    assert record.status == "confirmed"
    assert balance.current_balance == Decimal("2.00")
    assert db.scalar(select(LeaveBalanceTransaction).where(
        LeaveBalanceTransaction.action == "leave_confirmed"
    )).change_amount == Decimal("-3.00")

    cancel_leave(
        db, leave_record_id=record.id, reason="Leave dates changed", actor="admin",
    )
    assert record.status == "cancelled"
    assert balance.current_balance == Decimal("5.00")
    assert db.scalar(select(LeaveBalanceTransaction).where(
        LeaveBalanceTransaction.action == "leave_cancelled"
    )).change_amount == Decimal("3.00")
    assert db.scalar(select(AuditLog).where(
        AuditLog.action == "leave_cancelled"
    )) is not None


def test_leave_rejects_unfunded_and_overlapping_entries(db_session):
    db, employee_id = db_session
    leave_type = create_leave_type(
        db, code="casual", name="Casual", paid=True, deducts_balance=True,
    )
    db.flush()
    set_manual_balance(
        db, employee_id=employee_id, leave_type_id=leave_type.id,
        balance=Decimal("1"), reason="Manual starting balance", actor="admin",
    )
    with pytest.raises(ValueError, match="leave_balance_insufficient"):
        confirm_leave(
            db, employee_id=employee_id, leave_type_id=leave_type.id,
            start_date=date(2026, 4, 1), end_date=date(2026, 4, 2),
            days=Decimal("2"), reason="Too many days", actor="admin",
        )
    confirm_leave(
        db, employee_id=employee_id, leave_type_id=leave_type.id,
        start_date=date(2026, 4, 1), end_date=date(2026, 4, 1),
        days=Decimal("1"), reason="One day", actor="admin",
    )
    with pytest.raises(ValueError, match="leave_dates_overlap_existing_record"):
        confirm_leave(
            db, employee_id=employee_id, leave_type_id=leave_type.id,
            start_date=date(2026, 4, 1), end_date=date(2026, 4, 1),
            days=Decimal("1"), reason="Duplicate day", actor="admin",
        )


def test_leave_never_invents_a_balance_and_validates_dates_and_reason(db_session):
    db, employee_id = db_session
    leave_type = create_leave_type(
        db, code="sick", name="Sick leave", paid=True, deducts_balance=True,
    )
    db.flush()
    with pytest.raises(ValueError, match="leave_balance_insufficient"):
        confirm_leave(
            db, employee_id=employee_id, leave_type_id=leave_type.id,
            start_date=date(2026, 5, 1), end_date=date(2026, 5, 1),
            days=Decimal("0.5"), reason="Sick", actor="admin",
        )
    with pytest.raises(ValueError, match="leave_dates_or_days_invalid"):
        confirm_leave(
            db, employee_id=employee_id, leave_type_id=leave_type.id,
            start_date=date(2026, 5, 2), end_date=date(2026, 5, 1),
            days=Decimal("1"), reason="Invalid period", actor="admin",
        )
    with pytest.raises(ValueError, match="leave_reason_required"):
        confirm_leave(
            db, employee_id=employee_id, leave_type_id=leave_type.id,
            start_date=date(2026, 5, 1), end_date=date(2026, 5, 1),
            days=Decimal("0.5"), reason="  ", actor="admin",
        )
