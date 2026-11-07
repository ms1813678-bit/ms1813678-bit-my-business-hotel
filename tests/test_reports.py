"""Tests for the multi-sheet Excel reporting export."""
from datetime import date, datetime
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
    PayrollPayment,
    PayrollRun,
)
from app.reports import _excel_safe_value, build_hr_pdf, build_hr_workbook, validate_workbook


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=HYPERLINK(\"https://example.invalid\",\"click\")", "'=HYPERLINK(\"https://example.invalid\",\"click\")"),
        ("  +SUM(1,2)", "'  +SUM(1,2)"),
        ("-cmd|/C calc", "'-cmd|/C calc"),
        ("@SUM(A1:A2)", "'@SUM(A1:A2)"),
        ("Ordinary employee name", "Ordinary employee name"),
        (8000.0, 8000.0),
        (-12.5, -12.5),
    ],
)
def test_excel_export_treats_formula_like_text_as_literal(value, expected):
    assert _excel_safe_value(value) == expected


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
            "Payroll comparison",
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
                full_name='=HYPERLINK("https://example.invalid","Synthetic")',
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
            payroll_item = PayrollItem(
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
            )
            db.add(payroll_item)
            db.flush()
            db.add(PayrollPayment(
                payroll_item_id=payroll_item.id,
                amount=Decimal("3000.00"),
                paid_at=datetime(2026, 11, 5, 12, 0),
                method="cash",
                reference="Synthetic partial payment",
                created_by="test-admin",
            ))
            db.commit()

            content = build_hr_workbook(db, date(2026, 10, 1), date(2026, 10, 31))
            workbook = load_workbook(BytesIO(content), read_only=False, data_only=True)
            try:
                assert workbook["Employees"].cell(2, 1).value == "MY-REPORT-0001"
                assert workbook["Employees"].cell(2, 2).value == '\'=HYPERLINK("https://example.invalid","Synthetic")'
                assert workbook["Attendance"].cell(2, 2).value == "MY-REPORT-0001"
                assert workbook["Leave"].cell(2, 3).value == "سنوية"
                assert workbook["Payroll"].cell(2, 4).value == "MY-REPORT-0001"
                assert workbook["Payroll"].cell(2, 5).value == "Name at payroll time"
                assert workbook["Payroll"].cell(2, 22).value == 3000
                assert workbook["Payroll"].cell(2, 23).value == 5000
                comparison = workbook["Payroll comparison"]
                assert comparison.max_row == 2
                assert comparison.cell(2, 1).value == date(2026, 10, 1)
                assert comparison.cell(2, 3).value == "closed"
                assert comparison.cell(2, 4).value == 1
                assert comparison.cell(2, 5).value == 8000
                assert comparison.cell(2, 7).value == 8000
                assert comparison.cell(2, 8).value == 3000
                assert comparison.cell(2, 9).value == 5000
                assert comparison.cell(2, 10).value == 0
                assert comparison.cell(2, 11).value == 1
                assert comparison.cell(2, 12).value == 0
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
