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

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.main.SessionLocal", factory)
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
