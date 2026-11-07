"""Tests for the multi-sheet Excel reporting export."""
from datetime import date
from io import BytesIO

import pytest
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from decimal import Decimal

from app.db import Base
from app.dependencies import get_current_admin
from app.main import app
from app.models import AdminAccount
from app.models import (
    AttendanceDailyRecord,
    Department,
    Employee,
    LeaveRecord,
    LeaveType,
    PayrollItem,
    PayrollRun,
)
from app.reports import build_hr_pdf, build_hr_workbook, validate_workbook


def test_hr_report_contains_all_expected_sheets():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            content = build_hr_workbook(db, date(2026, 10, 1), date(2026, 10, 31))
        names = validate_workbook(content)
        assert names == [
            "Employees",
            "Attendance",
            "Leave",
            "Leave balances",
            "Sanctions rewards",
            "Advances",
            "Payroll",
            "Departments",
        ]
        workbook = load_workbook(BytesIO(content), read_only=False, data_only=True)
        try:
            assert workbook["Employees"].freeze_panes == "A2"
            assert workbook["Payroll"].max_row == 1
            assert workbook["Attendance"].max_row == 1
        finally:
            workbook.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_hr_report_rejects_reversed_date_range():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            with pytest.raises(ValueError, match="End date"):
                build_hr_workbook(db, date(2026, 11, 1), date(2026, 10, 1))
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()



def test_hr_report_preserves_employee_and_payroll_history():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            department = Department(name="Synthetic Reports Department", active=True)
            db.add(department)
            db.flush()
            employee = Employee(
                code="MY-REPORT-0001",
                full_name="Synthetic Report Employee",
                department_id=department.id,
                position="Receptionist",
                hire_date=date(2026, 1, 1),
                status="active",
                basic_salary=Decimal("8000.00"),
                daily_hours=Decimal("8"),
            )
            leave_type = LeaveType(
                code="annual",
                name_ar="سنوية",
                name_en="Annual",
                is_paid=True,
                tracks_balance=True,
                active=True,
            )
            db.add_all([employee, leave_type])
            db.flush()
            db.add(AttendanceDailyRecord(
                employee_id=employee.id,
                work_date=date(2026, 10, 5),
                status="approved",
                worked_minutes=480,
            ))
            db.add(LeaveRecord(
                employee_id=employee.id,
                leave_type_id=leave_type.id,
                start_date=date(2026, 10, 10),
                end_date=date(2026, 10, 11),
                calendar_days=2,
                charged_days=Decimal("2.00"),
                reason="Synthetic annual leave",
                status="confirmed",
                created_by="test-admin",
            ))
            run = PayrollRun(
                period_start=date(2026, 10, 1),
                period_end=date(2026, 10, 31),
                pay_date=date(2026, 11, 5),
                status="closed",
                created_by="test-admin",
            )
            db.add(run)
            db.flush()
            db.add(PayrollItem(
                payroll_run_id=run.id,
                employee_id=employee.id,
                employee_code_snapshot="MY-REPORT-0001",
                employee_name_snapshot="Name at payroll time",
                department_name_snapshot="Department at payroll time",
                position_snapshot="Position at payroll time",
                salary_basis=Decimal("8000.00"),
                base_salary=Decimal("8000.00"),
                daily_hours=Decimal("8"),
                net_pay=Decimal("8000.00"),
            ))
            db.commit()

            content = build_hr_workbook(db, date(2026, 10, 1), date(2026, 10, 31))
            workbook = load_workbook(BytesIO(content), read_only=False, data_only=True)
            try:
                assert workbook["Employees"].cell(2, 1).value == "MY-REPORT-0001"
                assert workbook["Attendance"].cell(2, 2).value == "MY-REPORT-0001"
                assert workbook["Leave"].cell(2, 3).value == "سنوية"
                assert workbook["Payroll"].cell(2, 4).value == "MY-REPORT-0001"
                assert workbook["Payroll"].cell(2, 5).value == "Name at payroll time"
            finally:
                workbook.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()



def test_hr_pdf_export_creates_a_valid_pdf_document():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            content = build_hr_pdf(db, date(2026, 10, 1), date(2026, 10, 31))
        assert content.startswith(b"%PDF-")
        assert len(content) > 1000
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()



def test_dashboard_displays_live_hr_workflow_metrics(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_admin():
        return AdminAccount(username="dashboard-admin", active=True)

    app.dependency_overrides[get_current_admin] = override_admin
    monkeypatch.setattr("app.main.SessionLocal", factory)
    try:
        with TestClient(app) as client:
            response = client.get("/")
        assert response.status_code == 200
        assert "الموظفون النشطون" in response.text
        assert "مسيرات الرواتب المفتوحة" in response.text
        assert "0" in response.text
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()
