"""Unit tests for HR Excel report generation."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Department, Employee
from app.reports_service import build_hr_workbook, validate_workbook


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with SessionLocal() as session:
        yield session


def test_build_hr_workbook_empty_range(db_session: Session):
    dept = Department(name="Reception", active=True)
    db_session.add(dept)
    db_session.flush()
    db_session.add(
        Employee(
            code="MY-0001",
            full_name="Test Employee",
            department_id=dept.id,
            position="Receptionist",
            hire_date=date(2025, 1, 1),
            status="active",
            basic_salary=Decimal("5000.00"),
            daily_hours=Decimal("8.00"),
        )
    )
    db_session.commit()

    data = build_hr_workbook(db_session, date(2026, 1, 1), date(2026, 1, 31))
    sheets = validate_workbook(data)
    assert "Employees" in sheets
    assert "Attendance" in sheets
    assert "Leave" in sheets
    assert "Advances" in sheets
    assert "Sanctions rewards" in sheets
    assert "Payroll" in sheets
    assert "Departments" in sheets


def test_end_before_start_raises(db_session: Session):
    with pytest.raises(ValueError, match="end_date_before_start_date"):
        build_hr_workbook(db_session, date(2026, 2, 1), date(2026, 1, 1))
