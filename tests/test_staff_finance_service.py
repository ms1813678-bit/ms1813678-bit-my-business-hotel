from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Department, Employee, SalaryAdvance, StaffAction
from app.staff_finance_service import (
    cancel_salary_advance, cancel_staff_action, create_salary_advance, create_staff_action,
    outstanding_advances_total,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        dept = Department(name="Finance Dept", active=True)
        db.add(dept)
        db.flush()
        emp = Employee(
            code="MY-0100", full_name="Advance Employee",
            department_id=dept.id, position="Clerk",
            hire_date=date(2026, 1, 1), status="active",
            basic_salary=Decimal("4000.00"), daily_hours=Decimal("8.00"),
        )
        db.add(emp)
        db.commit()
        yield db
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_advance_window_and_25_percent_limit(db_session):
    emp = db_session.scalar(select(Employee))
    with pytest.raises(ValueError, match="advance_outside_request_window"):
        create_salary_advance(
            db_session, employee_id=emp.id, request_date=date(2026, 11, 10),
            amount=Decimal("100"), reason="too early", actor="hr-admin",
        )
    first = create_salary_advance(
        db_session, employee_id=emp.id, request_date=date(2026, 11, 16),
        amount=Decimal("800"), reason="approved request", actor="hr-admin",
    )
    assert first.payroll_year == 2026 and first.payroll_month == 12
    assert outstanding_advances_total(db_session, emp.id) == Decimal("800.00")
    with pytest.raises(ValueError, match="advance_exceeds_outstanding_limit"):
        create_salary_advance(
            db_session, employee_id=emp.id, request_date=date(2026, 11, 18),
            amount=Decimal("300"), reason="would exceed 25 percent", actor="hr-admin",
        )


def test_cancel_advance_restores_capacity(db_session):
    emp = db_session.scalar(select(Employee))
    adv = create_salary_advance(
        db_session, employee_id=emp.id, request_date=date(2026, 11, 15),
        amount=Decimal("1000"), reason="need", actor="hr-admin",
    )
    cancel_salary_advance(db_session, advance_id=adv.id, reason="employee withdrew", actor="hr-admin")
    assert outstanding_advances_total(db_session, emp.id) == Decimal("0.00")
    create_salary_advance(
        db_session, employee_id=emp.id, request_date=date(2026, 11, 20),
        amount=Decimal("1000"), reason="retry", actor="hr-admin",
    )


def test_staff_action_requires_amount_for_financial_categories(db_session):
    emp = db_session.scalar(select(Employee))
    with pytest.raises(ValueError, match="staff_action_amount_required"):
        create_staff_action(
            db_session, employee_id=emp.id, action_kind="sanction",
            category="salary_deduction", action_date=date(2026, 11, 5),
            amount=None, reason="missing amount", actor="hr-admin",
        )
    action = create_staff_action(
        db_session, employee_id=emp.id, action_kind="reward",
        category="certificate", action_date=date(2026, 11, 5),
        amount=None, reason="excellent service", actor="hr-admin",
    )
    assert action.amount is None
    cancel_staff_action(db_session, action_id=action.id, reason="recorded in error", actor="hr-admin")
    assert db_session.get(StaffAction, action.id).status == "cancelled"
