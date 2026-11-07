from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    AttendanceMonth,
    Department,
    Employee,
    EmployeeServicePeriod,
    PayrollLine,
    PayrollPayment,
)
from app.payroll_workflow import (
    approve_payroll_period,
    close_payroll_period,
    create_payroll_period,
    month_bounds,
    payment_totals,
    record_payroll_payment,
    reopen_payroll_period,
    update_payroll_line,
)


@pytest.fixture()
def payroll_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as db:
        department = Department(name="Payroll Synthetic Department", active=True)
        db.add(department)
        db.flush()
        employee = Employee(
            code="MY-PAY-TEST-0001",
            full_name="Synthetic Payroll Employee",
            department_id=department.id,
            position="Test Position",
            hire_date=date(2024, 1, 1),
            status="active",
            basic_salary=Decimal("6000.00"),
            daily_hours=Decimal("8"),
        )
        db.add(employee)
        db.flush()
        db.add(EmployeeServicePeriod(
            employee_id=employee.id,
            start_date=date(2024, 1, 1),
            end_date=None,
            start_reason="Synthetic test hire",
        ))
        db.commit()
        yield db
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_month_bounds_and_due_date():
    assert month_bounds(date(2026, 11, 1)) == (
        date(2026, 11, 1), date(2026, 11, 30)
    )
    with pytest.raises(ValueError):
        month_bounds(date(2026, 11, 2))


def test_period_snapshots_employee_and_due_date(payroll_db):
    period = create_payroll_period(
        payroll_db, date(2026, 11, 1), "payroll-admin"
    )
    payroll_db.flush()
    line = payroll_db.scalar(select(PayrollLine))
    assert period.period_end == date(2026, 11, 30)
    assert period.payment_due_date == date(2026, 12, 5)
    assert period.status == "draft"
    assert line.employee_code_snapshot == "MY-PAY-TEST-0001"
    assert line.basic_salary_snapshot == Decimal("6000.00")
    assert line.net_pay == Decimal("6000.00")
    with pytest.raises(ValueError):
        create_payroll_period(payroll_db, date(2026, 11, 1), "payroll-admin")


def test_payroll_edit_approval_close_reopen_and_audit(payroll_db):
    period = create_payroll_period(payroll_db, date(2026, 11, 1), "payroll-admin")
    payroll_db.flush()
    line = payroll_db.scalar(select(PayrollLine))
    update_payroll_line(
        payroll_db, line, actor="payroll-admin",
        amounts={
            "attendance_bonus": Decimal("150.00"),
            "performance_bonus": Decimal("100.00"),
            "absence_deduction": Decimal("200.00"),
            "insurance_share": Decimal("300.00"),
        },
        notes="Synthetic payroll review",
    )
    assert line.net_pay == Decimal("5750.00")

    with pytest.raises(ValueError, match="Attendance"):
        approve_payroll_period(payroll_db, period, "payroll-admin")

    payroll_db.add(AttendanceMonth(
        period_start=period.period_start,
        period_end=period.period_end,
        status="approved",
        created_by="payroll-admin",
        approved_by="payroll-admin",
        approval_reason="Synthetic attendance approval",
    ))
    payroll_db.flush()
    approve_payroll_period(payroll_db, period, "payroll-admin")
    assert period.status == "approved"
    with pytest.raises(ValueError):
        update_payroll_line(
            payroll_db, line, actor="payroll-admin",
            amounts={"attendance_bonus": Decimal("200")},
        )
    close_payroll_period(payroll_db, period, "payroll-admin")
    assert period.status == "closed"
    reopen_payroll_period(
        payroll_db, period, "payroll-admin", "Synthetic correction reason"
    )
    assert period.status == "draft"
    assert period.reopen_reason == "Synthetic correction reason"


def test_partial_and_full_payments_cannot_exceed_net(payroll_db):
    period = create_payroll_period(payroll_db, date(2026, 11, 1), "payroll-admin")
    payroll_db.flush()
    line = payroll_db.scalar(select(PayrollLine))
    payroll_db.add(AttendanceMonth(
        period_start=period.period_start,
        period_end=period.period_end,
        status="approved",
        created_by="payroll-admin",
        approved_by="payroll-admin",
        approval_reason="Synthetic attendance approval",
    ))
    payroll_db.flush()
    approve_payroll_period(payroll_db, period, "payroll-admin")

    record_payroll_payment(
        payroll_db, line, amount=Decimal("2500"),
        payment_date=date(2026, 12, 5), payment_method="cash",
        actor="payroll-admin",
    )
    paid, outstanding = payment_totals(payroll_db, line.id)
    assert paid == Decimal("2500.00")
    assert outstanding == Decimal("3500.00")

    record_payroll_payment(
        payroll_db, line, amount=Decimal("3500"),
        payment_date=date(2026, 12, 6), payment_method="bank_transfer",
        actor="payroll-admin", reference="SYNTHETIC-TRANSFER",
    )
    paid, outstanding = payment_totals(payroll_db, line.id)
    assert paid == Decimal("6000.00")
    assert outstanding == Decimal("0.00")
    assert payroll_db.query(PayrollPayment).count() == 2

    with pytest.raises(ValueError, match="exceeds"):
        record_payroll_payment(
            payroll_db, line, amount=Decimal("1"),
            payment_date=date(2026, 12, 7), payment_method="cash",
            actor="payroll-admin",
        )
