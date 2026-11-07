"""Acceptance tests for separating active and former employees and preserving rehire history."""
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
    Department,
    Employee,
    EmployeeCodeCounter,
    EmployeeServicePeriod,
)
from app.security import hash_password


@pytest.fixture()
def lifecycle_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with factory() as db:
        admin = AdminAccount(
            username="lifecycle-admin",
            password_hash=hash_password("Lifecycle-Test-Password-123!"),
            active=True,
        )
        department = Department(name="Lifecycle Test Department", active=True)
        db.add_all([admin, department, EmployeeCodeCounter(id=1, last_number=0)])
        db.commit()
        department_id = department.id

    def override_get_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.main.SessionLocal", factory)

    try:
        with TestClient(app) as client:
            client.test_department_id = department_id
            client.test_session_factory = factory
            yield client
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def csrf_token(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, "Expected the page to render a CSRF token"
    return match.group(1)


def login(client):
    page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "username": "lifecycle-admin",
            "password": "Lifecycle-Test-Password-123!",
            "csrf_token": csrf_token(page),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def register_employee(client):
    page = client.get("/employees")
    response = client.post(
        "/employees",
        data={
            "csrf_token": csrf_token(page),
            "full_name": "Synthetic Lifecycle Employee",
            "department_id": str(client.test_department_id),
            "position": "Test Position",
            "hire_date": date.today().isoformat(),
            "basic_salary": str(Decimal("5000.00")),
            "daily_hours": "8",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with client.test_session_factory() as db:
        employee = db.scalar(
            select(Employee).where(Employee.full_name == "Synthetic Lifecycle Employee")
        )
        assert employee is not None
        return employee.id, employee.code


def section(page_text, heading):
    start = page_text.index(heading)
    start = page_text.rfind("<section", 0, start)
    end = page_text.index("</section>", start) + len("</section>")
    return page_text[start:end]


def test_archive_moves_employee_to_former_register_and_rehire_preserves_code_and_periods(
    lifecycle_client,
):
    client = lifecycle_client
    login(client)
    employee_id, original_code = register_employee(client)

    initial_page = client.get("/employees")
    active = section(initial_page.text, "الموظفون الحاليون")
    former = section(initial_page.text, "سجل الموظفين السابقين")
    assert "Synthetic Lifecycle Employee" in active
    assert "Synthetic Lifecycle Employee" not in former

    archive_token = csrf_token(initial_page)
    archived = client.post(
        f"/employees/{employee_id}/archive",
        data={"csrf_token": archive_token, "reason": "Synthetic end-of-service test"},
        follow_redirects=False,
    )
    assert archived.status_code == 303

    former_page = client.get("/employees")
    active = section(former_page.text, "الموظفون الحاليون")
    former = section(former_page.text, "سجل الموظفين السابقين")
    assert "Synthetic Lifecycle Employee" not in active
    assert "Synthetic Lifecycle Employee" in former
    assert original_code in former
    assert "Synthetic end-of-service test" in former

    new_hire_date = date.today() + timedelta(days=1)
    rehire = client.post(
        f"/employees/{employee_id}/rehire",
        data={
            "csrf_token": csrf_token(former_page),
            "new_hire_date": new_hire_date.isoformat(),
            "reason": "Synthetic rehire test",
        },
        follow_redirects=False,
    )
    assert rehire.status_code == 303

    reactivated_page = client.get("/employees")
    active = section(reactivated_page.text, "الموظفون الحاليون")
    former = section(reactivated_page.text, "سجل الموظفين السابقين")
    assert "Synthetic Lifecycle Employee" in active
    assert original_code in active
    assert "سجل فترات الخدمة" in active
    assert "Synthetic Lifecycle Employee" not in former

    with client.test_session_factory() as db:
        employee = db.get(Employee, employee_id)
        periods = db.scalars(
            select(EmployeeServicePeriod)
            .where(EmployeeServicePeriod.employee_id == employee_id)
            .order_by(EmployeeServicePeriod.start_date, EmployeeServicePeriod.id)
        ).all()

        assert employee.code == original_code
        assert employee.status == "active"
        assert employee.hire_date == new_hire_date
        assert len(periods) == 2
        assert periods[0].start_date == date.today()
        assert periods[0].end_date == date.today()
        assert periods[0].end_reason == "Synthetic end-of-service test"
        assert periods[1].start_date == new_hire_date
        assert periods[1].end_date is None
        assert periods[1].start_reason == "Synthetic rehire test"
