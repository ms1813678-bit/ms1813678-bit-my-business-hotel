from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import AdvanceRequest, Department, Employee
from app.advance_workflow import create_advance, outstanding_amount


@pytest.fixture()
def advance_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    with factory() as db:
        department = Department(name="Advance Test Department", active=True)
        db.add(department)
        db.flush()
        employee = Employee(
            code="MY-ADV-TEST-0001",
            full_name="Synthetic Advance Employee",
            department_id=department.id,
            position="Test Position",
            hire_date=date.today() - timedelta(days=365),
            status="active",
            basic_salary=Decimal("10000.00"),
            daily_hours=Decimal("8"),
        )
        db.add(employee)
        db.flush()
        yield db, employee.id
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_outstanding_advance_principal_is_global_across_payroll_months(advance_db):
    db, employee_id = advance_db
    rows = [
        ("pending", date(2026, 8, 1), Decimal("500.00")),
        ("approved", date(2026, 9, 1), Decimal("600.00")),
        ("paid", date(2026, 10, 1), Decimal("700.00")),
        ("rejected", date(2026, 10, 1), Decimal("800.00")),
        ("deducted", date(2026, 7, 1), Decimal("900.00")),
    ]
    for status, payroll_month, amount in rows:
        db.add(AdvanceRequest(
            employee_id=employee_id,
            request_date=date(2026, payroll_month.month, 19),
            payroll_month=payroll_month,
            amount=amount,
            salary_basis=Decimal("10000.00"),
            reason=f"Synthetic {status} advance",
            status=status,
            created_by="test",
        ))
    db.flush()

    assert outstanding_amount(db, employee_id) == Decimal("1800.00")


def test_twenty_five_percent_cap_applies_across_different_payroll_months(advance_db):
    db, employee_id = advance_db
    db.add(AdvanceRequest(
        employee_id=employee_id,
        request_date=date(2026, 9, 19),
        payroll_month=date(2026, 9, 1),
        amount=Decimal("2000.00"),
        salary_basis=Decimal("10000.00"),
        reason="Synthetic prior-month outstanding advance",
        status="pending",
        created_by="test",
    ))
    db.flush()

    with pytest.raises(ValueError, match="Outstanding advance limit"):
        create_advance(
            db,
            employee_id=employee_id,
            request_date=date(2026, 10, 19),
            amount=Decimal("600.00"),
            reason="Synthetic cross-month cap test",
            actor="test",
        )
