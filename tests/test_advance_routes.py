"""Acceptance tests for the salary-advance workflow and repayment month."""
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
from app.models import AdvanceInstallment, AdvanceRequest, AdminAccount, AuditLog, Department, Employee, EmployeeCodeCounter
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

    class FixedDate(real_date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 17)

    def override_get_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.main.SessionLocal", factory)
    monkeypatch.setattr("app.main.date", FixedDate)
    monkeypatch.setattr("app.advance_workflow.date", FixedDate)
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


def submit_advance(client, token, amount="1250.00", request_date="2026-11-17", advance_type="regular"):
    return client.post(
        "/advances",
        data={
            "csrf_token": token,
            "employee_id": client.test_employee_id,
            "request_date": request_date,
            "amount": amount,
            "advance_type": advance_type,
            "reason": "Synthetic advance test request",
        },
        follow_redirects=False,
    )


def test_regular_advance_cap_approval_payment_and_request_month(advance_client):
    client = advance_client
    login(client)
    token = csrf(client.get("/advances"))

    created = submit_advance(client, token)
    assert created.status_code == 303
    assert created.headers["location"] == "/advances"

    over_limit = submit_advance(client, token, amount="0.01")
    assert over_limit.status_code == 303
    assert "error=request" in over_limit.headers["location"]

    with client.test_session_factory() as db:
        request = db.scalar(select(AdvanceRequest))
        assert request.status == "pending"
        assert request.request_date == real_date(2026, 11, 17)
        assert request.payroll_month == real_date(2026, 11, 1)
        assert request.salary_basis == Decimal("5000.00")
        request_id = request.id

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
    repeated = client.post(
        f"/advances/{request_id}/paid",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert repeated.status_code == 303
    assert "error=paid" in repeated.headers["location"]

    with client.test_session_factory() as db:
        request = db.get(AdvanceRequest, request_id)
        assert request.status == "paid"
        assert request.paid_by == "advance-admin"
        actions = db.scalars(select(AuditLog.action)).all()
        assert "advance_requested" in actions
        assert "advance_approved" in actions
        assert "advance_paid" in actions


def test_rejected_advance_releases_monthly_reserved_limit(advance_client):
    client = advance_client
    login(client)
    token = csrf(client.get("/advances"))

    assert submit_advance(client, token).status_code == 303
    with client.test_session_factory() as db:
        request_id = db.scalar(select(AdvanceRequest.id))

    rejected = client.post(
        f"/advances/{request_id}/reject",
        data={"csrf_token": token, "reason": "Synthetic rejection reason"},
        follow_redirects=False,
    )
    assert rejected.status_code == 303

    second = submit_advance(client, token)
    assert second.status_code == 303
    with client.test_session_factory() as db:
        requests = db.scalars(select(AdvanceRequest).order_by(AdvanceRequest.id)).all()
        assert [item.status for item in requests] == ["rejected", "pending"]


@pytest.mark.parametrize("request_date", ["2026-11-14", "2026-10-15", "2026-12-15"])
def test_advance_date_must_be_in_current_month_and_day_15_to_22(advance_client, request_date):
    client = advance_client
    login(client)
    token = csrf(client.get("/advances"))
    response = submit_advance(client, token, request_date=request_date)
    assert response.status_code == 303
    assert "error=request" in response.headers["location"]
    with client.test_session_factory() as db:
        assert db.scalars(select(AdvanceRequest)).all() == []


def test_advance_page_and_mutations_require_admin_and_csrf(advance_client):
    client = advance_client
    assert client.get("/advances").status_code == 401
    assert client.post("/advances", data={}).status_code == 403
    login(client)
    assert client.get("/advances").status_code == 200
    assert client.post("/advances/1/approve", data={}).status_code == 403


def test_installment_advance_bypasses_25_percent_cap_and_creates_schedule(advance_client):
    client = advance_client
    login(client)
    token = csrf(client.get("/advances"))

    created = submit_advance(
        client, token, amount="6000.00", advance_type="installment"
    )
    assert created.status_code == 303

    with client.test_session_factory() as db:
        request = db.scalar(select(AdvanceRequest))
        assert request.advance_type == "installment"
        assert request.payroll_month == real_date(2026, 11, 1)
        request_id = request.id

    approved = client.post(
        f"/advances/{request_id}/approve",
        data={"csrf_token": token, "installment_count": "6"},
        follow_redirects=False,
    )
    assert approved.status_code == 303

    with client.test_session_factory() as db:
        request = db.get(AdvanceRequest, request_id)
        assert request.status == "approved"
        rows = db.execute(
            select(AdvanceInstallment).where(
                AdvanceInstallment.advance_request_id == request_id
            ).order_by(AdvanceInstallment.installment_number)
        ).scalars().all()
        assert [row.amount for row in rows] == [Decimal("1000.00")] * 6
        assert [row.payroll_month for row in rows] == [
            real_date(2026, 11, 1), real_date(2026, 12, 1),
            real_date(2027, 1, 1), real_date(2027, 2, 1),
            real_date(2027, 3, 1), real_date(2027, 4, 1),
        ]
