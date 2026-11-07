"""Acceptance tests for HR-only employee sanctions and rewards."""
import re
from datetime import date
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
    StaffAction,
)
from app.security import hash_password


@pytest.fixture()
def staff_action_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    with factory() as db:
        admin = AdminAccount(
            username="staff-action-admin",
            password_hash=hash_password("Staff-Action-Test-Password-123!"),
            active=True,
        )
        department = Department(name="Staff Action Test Department", active=True)
        db.add_all([admin, department, EmployeeCodeCounter(id=1, last_number=0)])
        db.flush()
        employee = Employee(
            code="MY-TEST-ACT-0001",
            full_name="Synthetic Staff Action Employee",
            department_id=department.id,
            position="Test Position",
            hire_date=date(2024, 1, 1),
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
            "username": "staff-action-admin",
            "password": "Staff-Action-Test-Password-123!",
            "csrf_token": csrf(page),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_financial_sanction_requires_approval_and_employee_acknowledgement(staff_action_client):
    client = staff_action_client
    assert client.get("/staff-actions").status_code == 401
    login(client)
    token = csrf(client.get("/staff-actions"))

    created = client.post(
        "/staff-actions",
        data={
            "csrf_token": token,
            "employee_id": client.test_employee_id,
            "kind": "sanction",
            "category": "salary_deduction",
            "title": "Synthetic salary deduction",
            "is_financial": "yes",
            "amount": "250.00",
            "payroll_month": "2026-12-01",
            "reason": "Synthetic documented reason",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303

    with client.test_session_factory() as db:
        item = db.scalar(select(StaffAction))
        action_id = item.id
        assert item.status == "draft"
        assert item.amount == Decimal("250.00")
        assert item.payroll_month == date(2026, 12, 1)
        assert item.acknowledgement_status == "pending"

    # Employee acknowledgement cannot be recorded before HR approval.
    early_ack = client.post(
        f"/staff-actions/{action_id}/acknowledgement",
        data={"csrf_token": token, "status": "viewed", "note": ""},
        follow_redirects=False,
    )
    assert early_ack.status_code == 303
    assert "error=ack" in early_ack.headers["location"]

    approved = client.post(
        f"/staff-actions/{action_id}/approve",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert approved.status_code == 303

    ack = client.post(
        f"/staff-actions/{action_id}/acknowledgement",
        data={
            "csrf_token": token,
            "status": "viewed",
            "note": "Employee reviewed the printed notice.",
        },
        follow_redirects=False,
    )
    assert ack.status_code == 303

    with client.test_session_factory() as db:
        item = db.get(StaffAction, action_id)
        assert item.status == "approved"
        assert item.acknowledgement_status == "viewed"
        assert item.acknowledgement_recorded_by == "staff-action-admin"
        assert item.acknowledgement_at is not None
        assert item.payroll_applied_at is None
        actions = db.scalars(select(AuditLog.action)).all()
        assert "sanction_created" in actions
        assert "sanction_approved" in actions
        assert "employee_action_acknowledgement_recorded" in actions


def test_nonfinancial_reward_does_not_create_payroll_amount(staff_action_client):
    client = staff_action_client
    login(client)
    token = csrf(client.get("/staff-actions"))
    response = client.post(
        "/staff-actions",
        data={
            "csrf_token": token,
            "employee_id": client.test_employee_id,
            "kind": "reward",
            "category": "certificate",
            "title": "Certificate of appreciation",
            "reason": "Synthetic performance recognition",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with client.test_session_factory() as db:
        item = db.scalar(select(StaffAction))
        assert item.kind == "reward"
        assert item.is_financial is False
        assert item.amount is None
        assert item.payroll_month is None


def test_financial_reward_requires_manual_amount_and_payroll_month(staff_action_client):
    client = staff_action_client
    login(client)
    token = csrf(client.get("/staff-actions"))
    response = client.post(
        "/staff-actions",
        data={
            "csrf_token": token,
            "employee_id": client.test_employee_id,
            "kind": "reward",
            "category": "financial_reward",
            "title": "Financial reward",
            "reason": "Synthetic reward reason",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=create" in response.headers["location"]
    with client.test_session_factory() as db:
        assert db.scalars(select(StaffAction)).all() == []


def test_staff_action_mutations_require_csrf(staff_action_client):
    client = staff_action_client
    assert client.post("/staff-actions", data={}).status_code == 403
    login(client)
    assert client.post("/staff-actions/1/approve", data={}).status_code == 403


def test_duplicate_active_financial_action_is_rejected(staff_action_client):
    client = staff_action_client
    login(client)
    token = csrf(client.get("/staff-actions"))
    payload = {
        "csrf_token": token,
        "employee_id": client.test_employee_id,
        "kind": "sanction",
        "category": "salary_deduction",
        "title": "Duplicate protection test",
        "is_financial": "yes",
        "amount": "125.00",
        "payroll_month": "2026-12-01",
        "reason": "Synthetic duplicate prevention",
    }

    first = client.post("/staff-actions", data=payload, follow_redirects=False)
    second = client.post("/staff-actions", data=payload, follow_redirects=False)

    assert first.status_code == 303
    assert first.headers["location"] == "/staff-actions"
    assert second.status_code == 303
    assert "error=create" in second.headers["location"]

    with client.test_session_factory() as db:
        actions = db.scalars(select(StaffAction)).all()
        assert len(actions) == 1
        assert actions[0].amount == Decimal("125.00")
