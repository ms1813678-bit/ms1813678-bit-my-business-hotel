"""Administrator-only payroll run, approval, close, and payment acceptance tests."""
import re
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import (
    AdvanceRequest,
    AdminAccount,
    AttendanceDailyRecord,
    AttendanceImportBatch,
    AttendanceMonth,
    AuditLog,
    Department,
    Employee,
    EmployeeCodeCounter,
    EmployeeServicePeriod,
    PayrollItem,
    PayrollPayment,
    PayrollRun,
    StaffAction,
)
from app.security import hash_password


@pytest.fixture()
def payroll_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    period_start = date(2026, 10, 1)
    period_end = date(2026, 10, 31)
    with factory() as db:
        admin = AdminAccount(
            username="payroll-admin",
            password_hash=hash_password("Payroll-Test-Password-123!"),
            active=True,
        )
        department = Department(name="Payroll Test Department", active=True)
        db.add_all([admin, department, EmployeeCodeCounter(id=1, last_number=0)])
        db.flush()
        employee = Employee(
            code="MY-TEST-PAY-0001",
            full_name="Synthetic Payroll Employee",
            department_id=department.id,
            position="Test Position",
            hire_date=date(2026, 10, 1),
            status="active",
            basic_salary=Decimal("9000.00"),
            daily_hours=Decimal("8"),
        )
        db.add(employee)
        db.flush()
        employee_id = employee.id
        db.add(EmployeeServicePeriod(
            employee_id=employee_id,
            start_date=period_start,
            end_date=None,
            start_reason="Synthetic test service period",
        ))
        program = AttendanceImportBatch(
            source_type="program_report",
            period_start=period_start,
            period_end=period_end,
            original_filename="synthetic-program.xlsx",
            file_sha256="a" * 64,
            uploaded_by="payroll-admin",
            status="approved",
            row_count=1,
            issue_count=0,
        )
        fingerprint = AttendanceImportBatch(
            source_type="fingerprint",
            period_start=period_start,
            period_end=period_end,
            original_filename="synthetic-fingerprint.xlsx",
            file_sha256="b" * 64,
            uploaded_by="payroll-admin",
            status="approved",
            row_count=1,
            issue_count=0,
        )
        db.add_all([program, fingerprint])
        db.flush()
        month = AttendanceMonth(
            period_start=period_start,
            period_end=period_end,
            program_import_batch_id=program.id,
            fingerprint_import_batch_id=fingerprint.id,
            status="approved",
            created_by="payroll-admin",
            approved_by="payroll-admin",
            approved_at=datetime.utcnow(),
            approval_reason="Synthetic approved attendance",
        )
        db.add(month)
        db.add(AttendanceDailyRecord(
            employee_id=employee_id,
            work_date=date(2026, 10, 5),
            scheduled_start_at=datetime(2026, 10, 5, 9, 0),
            scheduled_end_at=datetime(2026, 10, 5, 17, 0),
            approved_clock_in=datetime(2026, 10, 5, 9, 20),
            approved_clock_out=datetime(2026, 10, 5, 16, 50),
            status="approved",
            approved_by="payroll-admin",
            approved_at=datetime.utcnow(),
        ))
        db.add(AdvanceRequest(
            employee_id=employee_id,
            request_date=date(2026, 9, 16),
            payroll_month=period_start,
            amount=Decimal("500.00"),
            salary_basis=Decimal("9000.00"),
            reason="Synthetic advance",
            status="paid",
            created_by="payroll-admin",
            paid_by="payroll-admin",
            paid_at=datetime.utcnow(),
        ))
        db.add_all([
            StaffAction(
                employee_id=employee_id,
                kind="reward",
                category="financial_reward",
                title="Synthetic financial reward",
                is_financial=True,
                amount=Decimal("100.00"),
                payroll_month=period_start,
                reason="Synthetic reward",
                status="approved",
                created_by="payroll-admin",
                approved_by="payroll-admin",
                approved_at=datetime.utcnow(),
                acknowledgement_status="pending",
            ),
            StaffAction(
                employee_id=employee_id,
                kind="sanction",
                category="salary_deduction",
                title="Synthetic salary sanction",
                is_financial=True,
                amount=Decimal("50.00"),
                payroll_month=period_start,
                reason="Synthetic sanction",
                status="approved",
                created_by="payroll-admin",
                approved_by="payroll-admin",
                approved_at=datetime.utcnow(),
                acknowledgement_status="pending",
            ),
        ])
        db.commit()

    def override_get_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.main.SessionLocal", factory)
    try:
        with TestClient(app) as client:
            client.test_employee_id = employee_id
            client.test_session_factory = factory
            yield client
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def csrf(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match
    return match.group(1)


def login(client):
    page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "username": "payroll-admin",
            "password": "Payroll-Test-Password-123!",
            "csrf_token": csrf(page),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_payroll_run_calculates_and_closes_once_then_tracks_partial_payment(payroll_client):
    client = payroll_client
    assert client.get("/payroll").status_code == 401
    login(client)
    token = csrf(client.get("/payroll"))

    created = client.post(
        "/payroll",
        data={"csrf_token": token, "year": "2026", "month": "10"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert created.headers["location"].startswith("/payroll/runs/")
    run_id = int(created.headers["location"].rsplit("/", 1)[1])

    with client.test_session_factory() as db:
        run = db.get(PayrollRun, run_id)
        item = db.scalar(select(PayrollItem).where(PayrollItem.payroll_run_id == run_id))
        item_id = item.id
        assert run.pay_date == date(2026, 11, 5)
        assert run.status == "draft"
        assert item.employee_code_snapshot == "MY-TEST-PAY-0001"
        assert item.employee_name_snapshot == "Synthetic Payroll Employee"
        assert item.department_name_snapshot == "Payroll Test Department"
        assert item.position_snapshot == "Test Position"
        assert item.salary_basis == Decimal("9000.00")
        assert item.base_salary == Decimal("9000.00")
        assert item.late_minutes == 20
        assert item.early_minutes == 10
        assert item.lateness_deduction == Decimal("9.38")
        assert item.advance_deduction == Decimal("500.00")
        assert item.staff_reward_amount == Decimal("100.00")
        assert item.staff_sanction_amount == Decimal("50.00")

    # A financial source created after the payroll snapshot must not be
    # silently marked as consumed when this run closes.
    with client.test_session_factory() as db:
        db.add(AdvanceRequest(
            employee_id=client.test_employee_id,
            request_date=date(2026, 9, 17),
            payroll_month=date(2026, 10, 1),
            amount=Decimal("200.00"),
            salary_basis=Decimal("9000.00"),
            reason="Synthetic late-paid advance",
            status="paid",
            created_by="payroll-admin",
            paid_by="payroll-admin",
            paid_at=datetime.utcnow(),
        ))
        db.add(StaffAction(
            employee_id=client.test_employee_id,
            kind="reward",
            category="financial_reward",
            title="Late-added reward",
            is_financial=True,
            amount=Decimal("20.00"),
            payroll_month=date(2026, 10, 1),
            reason="Created after payroll snapshot",
            status="approved",
            created_by="payroll-admin",
            approved_by="payroll-admin",
            approved_at=datetime.utcnow(),
            acknowledgement_status="pending",
        ))
        db.commit()

    updated = client.post(
        f"/payroll/items/{item_id}/update",
        data={
            "csrf_token": token,
            "attendance_bonus": "100",
            "performance_bonus": "200",
            "occupancy_bonus": "50",
            "overtime_hours": "2",
            "other_bonus": "25",
            "absence_days": "1",
            "lateness_deduction": "9.38",
            "insurance_share": "200",
            "taxes": "50",
            "other_deductions": "10",
            "note": "Synthetic payroll review",
        },
        follow_redirects=False,
    )
    assert updated.status_code == 303

    with client.test_session_factory() as db:
        item = db.get(PayrollItem, item_id)
        assert item.overtime_amount == Decimal("75.00")
        assert item.absence_deduction == Decimal("290.32")
        assert item.net_pay == Decimal("8440.30")

    approved = client.post(
        f"/payroll/runs/{run_id}/approve",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert approved.status_code == 303
    closed = client.post(
        f"/payroll/runs/{run_id}/close",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert closed.status_code == 303

    with client.test_session_factory() as db:
        run = db.get(PayrollRun, run_id)
        advances = db.scalars(select(AdvanceRequest).order_by(AdvanceRequest.id)).all()
        actions = db.scalars(select(StaffAction).order_by(StaffAction.id)).all()
        assert run.status == "closed"
        assert advances[0].status == "deducted"
        assert advances[0].payroll_run_id == run_id
        assert advances[1].status == "paid"
        assert advances[1].payroll_run_id is None
        assert all(action.payroll_run_id == run_id for action in actions[:2])
        assert all(action.payroll_applied_at is not None for action in actions[:2])
        assert actions[2].payroll_run_id is None
        assert actions[2].payroll_applied_at is None

    partial = client.post(
        f"/payroll/items/{item_id}/payments",
        data={
            "csrf_token": token,
            "amount": "4000",
            "paid_date": "2026-11-05",
            "method": "cash",
            "reference": "Synthetic cash voucher",
        },
        follow_redirects=False,
    )
    assert partial.status_code == 303

    overpayment = client.post(
        f"/payroll/items/{item_id}/payments",
        data={
            "csrf_token": token,
            "amount": "5000",
            "paid_date": "2026-11-06",
            "method": "bank_transfer",
            "reference": "Synthetic overpayment",
        },
        follow_redirects=False,
    )
    assert overpayment.status_code == 303
    assert "error=payment" in overpayment.headers["location"]

    final_payment = client.post(
        f"/payroll/items/{item_id}/payments",
        data={
            "csrf_token": token,
            "amount": "4440.30",
            "paid_date": "2026-11-06",
            "method": "bank_transfer",
            "reference": "Synthetic final transfer",
        },
        follow_redirects=False,
    )
    assert final_payment.status_code == 303

    with client.test_session_factory() as db:
        item = db.get(PayrollItem, item_id)
        payments = db.scalars(select(PayrollPayment)).all()
        assert len(payments) == 2
        assert sum((p.amount for p in payments), Decimal("0.00")) == Decimal("8440.30")

    reopened = client.post(
        f"/payroll/runs/{run_id}/reopen",
        data={"csrf_token": token, "reason": "Synthetic correction review"},
        follow_redirects=False,
    )
    assert reopened.status_code == 303

    cannot_edit_paid = client.post(
        f"/payroll/items/{item_id}/update",
        data={
            "csrf_token": token,
            "attendance_bonus": "0",
            "performance_bonus": "0",
            "occupancy_bonus": "0",
            "overtime_hours": "0",
            "other_bonus": "0",
            "absence_days": "0",
            "lateness_deduction": "0",
            "insurance_share": "0",
            "taxes": "0",
            "other_deductions": "0",
            "note": "Should not change after payment",
        },
        follow_redirects=False,
    )
    assert cannot_edit_paid.status_code == 303
    assert "error=item" in cannot_edit_paid.headers["location"]


def test_payroll_creation_requires_approved_attendance(payroll_client):
    client = payroll_client
    login(client)
    token = csrf(client.get("/payroll"))
    with client.test_session_factory() as db:
        month = db.scalar(select(AttendanceMonth))
        month.status = "under_review"
        db.commit()

    response = client.post(
        "/payroll",
        data={"csrf_token": token, "year": "2026", "month": "10"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=create" in response.headers["location"]
    with client.test_session_factory() as db:
        assert db.scalars(select(PayrollRun)).all() == []


def test_payroll_mutations_require_csrf(payroll_client):
    client = payroll_client
    assert client.post("/payroll", data={}).status_code == 403
    login(client)
    assert client.get("/payroll").status_code == 200



def test_payroll_prorates_new_hire_by_actual_calendar_days(payroll_client):
    client = payroll_client
    login(client)
    with client.test_session_factory() as db:
        department = db.scalar(select(Department))
        employee = Employee(
            code="MY-TEST-PAY-0002",
            full_name="Synthetic Midmonth Employee",
            department_id=department.id,
            position="Test Position",
            hire_date=date(2026, 10, 16),
            status="active",
            basic_salary=Decimal("9000.00"),
            daily_hours=Decimal("8"),
        )
        db.add(employee)
        db.flush()
        db.add(EmployeeServicePeriod(
            employee_id=employee.id,
            start_date=date(2026, 10, 16),
            end_date=None,
            start_reason="Synthetic midmonth hire",
        ))
        db.commit()
        employee_id = employee.id

    token = csrf(client.get("/payroll"))
    response = client.post(
        "/payroll",
        data={"csrf_token": token, "year": "2026", "month": "10"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    run_id = int(response.headers["location"].rsplit("/", 1)[1])
    with client.test_session_factory() as db:
        item = db.scalar(
            select(PayrollItem).where(
                PayrollItem.payroll_run_id == run_id,
                PayrollItem.employee_id == employee_id,
            )
        )
        assert item is not None
        assert item.base_salary == Decimal("4645.16")
        assert item.employee_name_snapshot == "Synthetic Midmonth Employee"
