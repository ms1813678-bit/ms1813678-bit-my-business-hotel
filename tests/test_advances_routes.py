"""Administrator-only acceptance tests for advances and repayment scheduling."""
import re
from datetime import date as real_date
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
)
from app.security import hash_password


@pytest.fixture()
def advance_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with factory() as db:
        admin = AdminAccount(
            username="advance-admin",
            password_hash=hash_password("Advance-Test-Password-123!"),
            active=True,
        )
        department = Department(name="Advance Test Department", active=True)
        db.add_all([admin, department, EmployeeCodeCounter(id=1, last_number=0)])
        db.flush()
        employee = Employee(
            code="MY-TEST-ADV-0001",
            full_name="Synthetic Advance Employee",
            department_id=department.id,
            position="Test Position",
            hire_date=real_date(2024, 1, 1),
            status="active",
            basic_salary=Decimal("5000.00"),
            daily_hours=Decimal("8"),
        )
        db.add(employee)
        db.commit()
        employee_id = employee.id

    def override_get_db():
        with factory() as db:
            yield db

    class FixedDate(real_date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 17)

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.main.SessionLocal", factory)
    monkeypatch.setattr("app.main.date", FixedDate)
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
            "username": "advance-admin",
            "password": "Advance-Test-Password-123!",
            "csrf_token": csrf(page),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_advance_cap_window_and_next_month_repayment(advance_client):
    client = advance_client
    login(client)
    token = csrf(client.get("/advances"))

    created = client.post(
        "/advances",
        data={
            "csrf_token": token,
            "employee_id": client.test_employee_id,
            "amount": "1250.00",
            "reason": "Synthetic advance request",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert created.headers["location"] == "/advances"

    over_limit = client.post(
        "/advances",
        data={
            "csrf_token": token,
            "employee_id": client.test_employee_id,
            "amount": "0.01",
            "reason": "Synthetic over-limit request",
        },
        follow_redirects=False,
    )
    assert over_limit.status_code == 303
    assert "error=request" in over_limit.headers["location"]

    with client.test_session_factory() as db:
        item = db.scalar(select(AdvanceRequest))
        assert item.status == "pending"
        assert item.request_date == real_date(2026, 11, 17)
        assert item.salary_month == real_date(2026, 11, 1)
        assert item.repayment_month == real_date(2026, 12, 1)
        advance_id = item.id

    approved = client.post(
        f"/advances/{advance_id}/approve",
        data={"csrf_token": token, "reason": "Approved by synthetic test"},
        follow_redirects=False,
    )
    assert approved.status_code == 303

    disbursed = client.post(
        f"/advances/{advance_id}/disburse",
        data={"csrf_token": token, "reason": "Cash voucher TEST-001"},
        follow_redirects=False,
    )
    assert disbursed.status_code == 303

    repeated = client.post(
        f"/advances/{advance_id}/disburse",
        data={"csrf_token": token, "reason": "Repeated disbursement"},
        follow_redirects=False,
    )
    assert repeated.status_code == 303
    assert "error=disburse" in repeated.headers["location"]

    with client.test_session_factory() as db:
        item = db.get(AdvanceRequest, advance_id)
        assert item.status == "disbursed"
        assert item.disbursed_by == "advance-admin"
        actions = db.scalars(select(AuditLog.action)).all()
        assert "advance_requested" in actions
        assert "advance_approved" in actions
        assert "advance_disbursed" in actions


def test_advance_request_outside_days_15_to_22_is_rejected(advance_client, monkeypatch):
    client = advance_client
    login(client)
    token = csrf(client.get("/advances"))

    class OutsideWindowDate(real_date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 14)

    monkeypatch.setattr("app.main.date", OutsideWindowDate)
    response = client.post(
        "/advances",
        data={
            "csrf_token": token,
            "employee_id": client.test_employee_id,
            "amount": "500",
            "reason": "Synthetic outside-window request",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=request" in response.headers["location"]
    with client.test_session_factory() as db:
        assert db.scalars(select(AdvanceRequest)).all() == []


def test_advance_mutations_require_admin_and_csrf(advance_client):
    client = advance_client
    assert client.get("/advances").status_code == 401
    assert client.post("/advances", data={}).status_code == 403
    login(client)
    assert client.get("/advances").status_code == 200
    assert client.post("/advances/1/approve", data={}).status_code == 403
