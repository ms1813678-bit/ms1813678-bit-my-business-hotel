"""Route acceptance tests for administrator-managed leave and balance history."""
import re
from datetime import date, timedelta
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
    AuditLog,
    Department,
    Employee,
    EmployeeCodeCounter,
    LeaveBalanceTransaction,
    LeaveRecord,
    LeaveType,
)
from app.security import hash_password


@pytest.fixture()
def leave_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with factory() as db:
        admin = AdminAccount(
            username="leave-admin",
            password_hash=hash_password("Leave-Test-Password-123!"),
            active=True,
        )
        department = Department(name="Leave Test Department", active=True)
        db.add_all([admin, department, EmployeeCodeCounter(id=1, last_number=0)])
        db.flush()
        employee = Employee(
            code="MY-TEST-0001",
            full_name="Synthetic Leave Employee",
            department_id=department.id,
            position="Test Position",
            hire_date=date.today() - timedelta(days=365),
            status="active",
            basic_salary=Decimal("5000.00"),
            daily_hours=Decimal("8"),
        )
        annual = LeaveType(
            code="annual", name_ar="سنوية", name_en="Annual",
            is_paid=True, tracks_balance=True, active=True,
        )
        sick = LeaveType(
            code="sick", name_ar="مرضية", name_en="Sick",
            is_paid=True, tracks_balance=True, active=True,
        )
        casual = LeaveType(
            code="casual", name_ar="عارضة", name_en="Casual",
            is_paid=True, tracks_balance=True, active=True,
        )
        db.add_all([employee, annual, sick, casual])
        db.commit()
        employee_id = employee.id
        annual_id = annual.id
        casual_id = casual.id

    def override_get_db():
        with factory() as db:
            yield db

    class FixedAdvanceDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 17)

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.main.SessionLocal", factory)
    monkeypatch.setattr("app.main.date", FixedAdvanceDate)
    monkeypatch.setattr("app.advance_workflow.date", FixedAdvanceDate)
    try:
        with TestClient(app) as client:
            client.test_employee_id = employee_id
            client.test_annual_id = annual_id
            client.test_casual_id = casual_id
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
            "username": "leave-admin",
            "password": "Leave-Test-Password-123!",
            "csrf_token": csrf(page),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_leave_balance_is_deducted_once_and_restored_on_cancellation(leave_client):
    client = leave_client
    login(client)
    token = csrf(client.get("/leave"))

    opening = client.post(
        "/leave/balances",
        data={
            "csrf_token": token,
            "employee_id": client.test_employee_id,
            "leave_type_id": client.test_annual_id,
            "transaction_type": "opening",
            "days_delta": "10",
            "reason": "Synthetic opening balance",
        },
        follow_redirects=False,
    )
    assert opening.status_code == 303

    start = date.today() + timedelta(days=10)
    created = client.post(
        "/leave/records",
        data={
            "csrf_token": token,
            "employee_id": client.test_employee_id,
            "leave_type_id": client.test_annual_id,
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=2)).isoformat(),
            "charged_days": "3",
            "reason": "Synthetic planned annual leave",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303

    with client.test_session_factory() as db:
        record = db.scalar(select(LeaveRecord))
        record_id = record.id
        assert record.status == "draft"
        assert record.calendar_days == 3

    confirmed = client.post(
        f"/leave/records/{record_id}/confirm",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert confirmed.status_code == 303

    # A second confirmation cannot create a second deduction.
    repeated = client.post(
        f"/leave/records/{record_id}/confirm",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert repeated.status_code == 303
    assert "error=confirm" in repeated.headers["location"]

    with client.test_session_factory() as db:
        record = db.get(LeaveRecord, record_id)
        transactions = db.scalars(
            select(LeaveBalanceTransaction).where(
                LeaveBalanceTransaction.leave_record_id == record_id,
                LeaveBalanceTransaction.transaction_type == "leave_use",
            )
        ).all()
        assert record.status == "confirmed"
        assert len(transactions) == 1
        assert Decimal(str(transactions[0].days_delta)) == Decimal("-3.00")

    cancelled = client.post(
        f"/leave/records/{record_id}/cancel",
        data={"csrf_token": token, "reason": "Synthetic cancellation"},
        follow_redirects=False,
    )
    assert cancelled.status_code == 303

    repeated_cancel = client.post(
        f"/leave/records/{record_id}/cancel",
        data={"csrf_token": token, "reason": "Repeated cancellation"},
        follow_redirects=False,
    )
    assert repeated_cancel.status_code == 303
    assert "error=cancel" in repeated_cancel.headers["location"]

    with client.test_session_factory() as db:
        record = db.get(LeaveRecord, record_id)
        entries = db.scalars(
            select(LeaveBalanceTransaction).where(
                LeaveBalanceTransaction.employee_id == client.test_employee_id,
                LeaveBalanceTransaction.leave_type_id == client.test_annual_id,
            )
        ).all()
        assert record.status == "cancelled"
        assert len(entries) == 3
        assert sum((Decimal(str(item.days_delta)) for item in entries), Decimal("0")) == Decimal("10.00")
        actions = db.scalars(select(AuditLog.action)).all()
        assert "leave_confirmed" in actions
        assert "leave_cancelled" in actions


def test_insufficient_balance_and_short_notice_are_not_approved(leave_client):
    client = leave_client
    login(client)
    token = csrf(client.get("/leave"))
    start = date.today() + timedelta(days=1)

    short_notice = client.post(
        "/leave/records",
        data={
            "csrf_token": token,
            "employee_id": client.test_employee_id,
            "leave_type_id": client.test_annual_id,
            "start_date": start.isoformat(),
            "end_date": start.isoformat(),
            "charged_days": "1",
            "reason": "Synthetic short-notice test",
        },
        follow_redirects=False,
    )
    assert short_notice.status_code == 303
    assert "error=notice" in short_notice.headers["location"]

    emergency = client.post(
        "/leave/records",
        data={
            "csrf_token": token,
            "employee_id": client.test_employee_id,
            "leave_type_id": client.test_casual_id,
            "start_date": start.isoformat(),
            "end_date": start.isoformat(),
            "charged_days": "1",
            "reason": "Synthetic emergency exception",
        },
        follow_redirects=False,
    )
    assert emergency.status_code == 303
    with client.test_session_factory() as db:
        record = db.scalar(select(LeaveRecord))
        record_id = record.id
        assert record.status == "draft"

    insufficient = client.post(
        f"/leave/records/{record_id}/confirm",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert insufficient.status_code == 303
    assert "error=confirm" in insufficient.headers["location"]
    with client.test_session_factory() as db:
        record = db.get(LeaveRecord, record_id)
        assert record.status == "draft"
        assert db.scalars(select(LeaveBalanceTransaction)).all() == []


def test_leave_page_and_mutations_require_the_single_admin_and_csrf(leave_client):
    client = leave_client
    assert client.get("/leave").status_code == 401
    assert client.post("/leave/records", data={}).status_code == 403
    login(client)
    assert client.get("/leave").status_code == 200
    assert client.post("/leave/balances", data={}).status_code == 403


def test_overlapping_leave_records_for_same_employee_are_rejected(leave_client):
    client = leave_client
    login(client)
    token = csrf(client.get("/leave"))
    start = date.today() + timedelta(days=14)

    first = client.post(
        "/leave/records",
        data={
            "csrf_token": token,
            "employee_id": client.test_employee_id,
            "leave_type_id": client.test_annual_id,
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=2)).isoformat(),
            "charged_days": "3",
            "reason": "Synthetic first leave",
        },
        follow_redirects=False,
    )
    assert first.status_code == 303
    assert first.headers["location"] == "/leave"

    overlapping = client.post(
        "/leave/records",
        data={
            "csrf_token": token,
            "employee_id": client.test_employee_id,
            "leave_type_id": client.test_casual_id,
            "start_date": (start + timedelta(days=1)).isoformat(),
            "end_date": (start + timedelta(days=1)).isoformat(),
            "charged_days": "1",
            "reason": "Synthetic overlapping emergency leave",
        },
        follow_redirects=False,
    )
    assert overlapping.status_code == 303
    assert "error=overlap" in overlapping.headers["location"]

    with client.test_session_factory() as db:
        records = db.scalars(select(LeaveRecord)).all()
        assert len(records) == 1
        assert records[0].start_date == start


def valid_advance_date():
    # Synthetic date window is fixed to 17 November 2026 by leave_client.
    return date(2026, 11, 16)


def test_advance_window_cap_approval_payment_and_next_payroll_month(leave_client):
    from app.advance_workflow import next_payroll_month

    client = leave_client
    login(client)
    token = csrf(client.get("/advances"))
    requested_on = valid_advance_date()

    first = client.post(
        "/advances",
        data={
            "csrf_token": token,
            "employee_id": client.test_employee_id,
            "request_date": requested_on.isoformat(),
            "amount": "1200.00",
            "reason": "Synthetic advance request",
        },
        follow_redirects=False,
    )
    assert first.status_code == 303
    assert first.headers["location"] == "/advances"

    over_limit = client.post(
        "/advances",
        data={
            "csrf_token": token,
            "employee_id": client.test_employee_id,
            "request_date": requested_on.isoformat(),
            "amount": "100.00",
            "reason": "Synthetic request exceeding remaining cap",
        },
        follow_redirects=False,
    )
    assert over_limit.status_code == 303
    assert "error=request" in over_limit.headers["location"]

    with client.test_session_factory() as db:
        request = db.scalar(select(AdvanceRequest))
        request_id = request.id
        assert request.status == "pending"
        assert request.salary_basis == Decimal("5000.00")
        assert request.payroll_month == next_payroll_month(requested_on)

    approved = client.post(
        f"/advances/{request_id}/approve",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert approved.status_code == 303

    paid = client.post(
        f"/advances/{request_id}/paid",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert paid.status_code == 303

    repeated_paid = client.post(
        f"/advances/{request_id}/paid",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert repeated_paid.status_code == 303
    assert "error=paid" in repeated_paid.headers["location"]

    # A paid advance remains outstanding until payroll actually deducts it,
    # even when a later request belongs to a different request/payroll month.
    if requested_on.month == 1:
        other_month = date(requested_on.year - 1, 12, 16)
    else:
        other_month = date(requested_on.year, requested_on.month - 1, 16)
    second_month_request = client.post(
        "/advances",
        data={
            "csrf_token": token,
            "employee_id": client.test_employee_id,
            "request_date": other_month.isoformat(),
            "amount": "100.00",
            "reason": "Synthetic cross-month outstanding cap test",
        },
        follow_redirects=False,
    )
    assert second_month_request.status_code == 303
    assert "error=request" in second_month_request.headers["location"]

    with client.test_session_factory() as db:
        request = db.get(AdvanceRequest, request_id)
        assert request.status == "paid"
        assert request.amount == Decimal("1200.00")
        assert request.deducted_at is None
        actions = db.scalars(select(AuditLog.action)).all()
        assert "advance_requested" in actions
        assert "advance_approved" in actions
        assert "advance_paid" in actions


def test_advance_request_outside_window_and_unauthorized_access_are_rejected(leave_client):
    client = leave_client
    assert client.get("/advances").status_code == 401
    assert client.post("/advances", data={}).status_code == 403
    login(client)
    token = csrf(client.get("/advances"))
    requested_on = valid_advance_date()
    invalid_date = requested_on.replace(day=14)

    response = client.post(
        "/advances",
        data={
            "csrf_token": token,
            "employee_id": client.test_employee_id,
            "request_date": invalid_date.isoformat(),
            "amount": "500.00",
            "reason": "Synthetic invalid-window request",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=request" in response.headers["location"]
    with client.test_session_factory() as db:
        assert db.scalars(select(AdvanceRequest)).all() == []

def test_balance_entry_requires_nonblank_reason(leave_client):
    from app.leave_workflow import add_balance_entry

    client = leave_client
    with client.test_session_factory() as db:
        leave_type = db.get(LeaveType, client.test_annual_id)
        with pytest.raises(ValueError, match="reason is required"):
            add_balance_entry(
                db,
                employee_id=client.test_employee_id,
                leave_type=leave_type,
                transaction_type="opening",
                days_delta=Decimal("5"),
                reason="   ",
                actor="leave-admin",
            )

