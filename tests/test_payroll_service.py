from calendar import monthrange
from datetime import date, datetime, time
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    AttendanceDailyRecord, AttendanceMonth, Department, Employee, EmployeeServicePeriod,
    PayrollLine, PayrollPayment, PayrollPeriod, ShiftPattern,
)
from app.payroll_math import absence_deduction, lateness_deduction, money
from app.payroll_service import (
    approve_payroll, close_payroll, create_payroll_period, record_payment,
    reopen_payroll, update_payroll_line,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        dept = Department(name="Payroll Dept", active=True)
        db.add(dept)
        db.flush()
        emp = Employee(
            code="MY-0001", full_name="Payroll Employee",
            department_id=dept.id, position="Clerk",
            hire_date=date(2026, 1, 1), status="active",
            basic_salary=Decimal("6000.00"), daily_hours=Decimal("8.00"),
        )
        db.add(emp)
        db.flush()
        db.add(EmployeeServicePeriod(
            employee_id=emp.id, start_date=date(2026, 1, 1),
            start_reason="Initial employment",
        ))
        pattern = ShiftPattern(
            department_id=dept.id, code="DAY", name="Day",
            start_time=time(8, 0), end_time=time(16, 0),
            match_before_minutes=60, match_after_minutes=60, active=True,
        )
        db.add(pattern)
        db.commit()
        yield db
    Base.metadata.drop_all(engine)
    engine.dispose()


def _approve_attendance(db, year, month):
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    att = AttendanceMonth(
        period_start=start, period_end=end, status="approved",
        created_by="hr-admin", approved_by="hr-admin",
        approved_at=datetime.utcnow(), approval_reason="test",
    )
    db.add(att)
    db.flush()
    emp = db.scalar(select(Employee).where(Employee.code == "MY-0001"))
    pattern = db.scalar(select(ShiftPattern))
    db.add(AttendanceDailyRecord(
        employee_id=emp.id, work_date=start, shift_pattern_id=pattern.id,
        scheduled_start_at=datetime(year, month, 1, 8, 0),
        scheduled_end_at=datetime(year, month, 1, 16, 0),
        approved_clock_in=datetime(year, month, 1, 8, 20),
        approved_clock_out=datetime(year, month, 1, 15, 50),
        worked_minutes=450, status="approved", approved_by="hr-admin",
        approved_at=datetime.utcnow(),
    ))
    db.flush()
    return att


def test_create_requires_approved_attendance(db_session):
    with pytest.raises(ValueError, match="attendance_month_not_approved"):
        create_payroll_period(db_session, year=2026, month=11, actor="hr-admin")


def test_create_snapshot_and_lateness_from_approved_days(db_session):
    _approve_attendance(db_session, 2026, 11)
    period = create_payroll_period(db_session, year=2026, month=11, actor="hr-admin")
    db_session.commit()
    line = db_session.scalar(select(PayrollLine).where(PayrollLine.payroll_period_id == period.id))
    assert line is not None
    assert line.employee_code == "MY-0001"
    assert line.late_minutes == 20
    assert line.early_minutes == 10
    expected_late = lateness_deduction(line.base_salary, line.daily_hours, 20, 10)
    assert line.lateness_deduction == expected_late
    assert line.absence_days == 0
    assert line.net_pay == money(line.base_salary - expected_late)


def test_absence_is_manual_and_recalculates(db_session):
    _approve_attendance(db_session, 2026, 11)
    period = create_payroll_period(db_session, year=2026, month=11, actor="hr-admin")
    line = db_session.scalar(select(PayrollLine))
    update_payroll_line(
        db_session, line_id=line.id,
        values={"absence_days": Decimal("2")},
        reason="Confirmed unpaid absence after review",
        actor="hr-admin",
    )
    db_session.commit()
    line = db_session.get(PayrollLine, line.id)
    assert line.absence_deduction == absence_deduction(line.base_salary, 2026, 11, 2)


def test_approve_close_reopen_and_payment(db_session):
    _approve_attendance(db_session, 2026, 11)
    period = create_payroll_period(db_session, year=2026, month=11, actor="hr-admin")
    line = db_session.scalar(select(PayrollLine))
    approve_payroll(db_session, period_id=period.id, reason="Reviewed", actor="hr-admin")
    close_payroll(db_session, period_id=period.id, actor="hr-admin")
    reopen_payroll(db_session, period_id=period.id, reason="Correct overtime", actor="hr-admin")
    approve_payroll(db_session, period_id=period.id, reason="Re-reviewed", actor="hr-admin")
    payment = record_payment(
        db_session, line_id=line.id, amount=line.net_pay,
        paid_on=date(2026, 12, 5), method="cash", reference="R1", actor="hr-admin",
    )
    db_session.commit()
    line = db_session.get(PayrollLine, line.id)
    assert line.payment_status == "paid"
    assert db_session.get(PayrollPayment, payment.id).method == "cash"


def test_locked_period_rejects_line_edits(db_session):
    _approve_attendance(db_session, 2026, 11)
    period = create_payroll_period(db_session, year=2026, month=11, actor="hr-admin")
    line = db_session.scalar(select(PayrollLine))
    approve_payroll(db_session, period_id=period.id, reason="done", actor="hr-admin")
    with pytest.raises(ValueError, match="payroll_period_locked"):
        update_payroll_line(
            db_session, line_id=line.id, values={"absence_days": Decimal("1")},
            reason="too late", actor="hr-admin",
        )
