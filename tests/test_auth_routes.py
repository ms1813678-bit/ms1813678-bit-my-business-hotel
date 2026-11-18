import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main
from app.db import Base
from app.models import AdminAccount, AuditLog, Department, Employee, EmployeeChange
from scripts.create_admin import create_admin
from app.security import hash_password, verify_password


@pytest.fixture
def app_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Department(name="Reception", active=True))
        db.add(AdminAccount(
            username="hr-admin",
            password_hash=hash_password("HotelAdmin-Strong-2026!"),
            active=True,
        ))
        db.commit()

    monkeypatch.setattr(main, "SessionLocal", factory)
    client = TestClient(main.app, follow_redirects=False)
    yield client, factory
    client.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def test_login_requires_csrf_and_only_active_admin_authenticates(app_client):
    client, _ = app_client
    response = client.get("/login")
    token = csrf_from(response.text)

    forged = client.post("/login", data={
        "username": "hr-admin",
        "password": "HotelAdmin-Strong-2026!",
        "csrf_token": "attacker-token",
    })
    assert forged.status_code == 403

    login = client.post("/login", data={
        "username": "hr-admin",
        "password": "wrong-password",
        "csrf_token": token,
    })
    assert login.status_code == 401

    login = client.post("/login", data={
        "username": "hr-admin",
        "password": "HotelAdmin-Strong-2026!",
        "csrf_token": token,
    })
    assert login.status_code == 303
    assert login.headers["location"] == "/"

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "hr-admin" in dashboard.text


def test_sessions_are_revoked_when_admin_session_version_changes(app_client):
    client, factory = app_client
    token = csrf_from(client.get("/login").text)
    response = client.post("/login", data={
        "username": "hr-admin",
        "password": "HotelAdmin-Strong-2026!",
        "csrf_token": token,
    })
    assert response.status_code == 303
    assert client.get("/").status_code == 200

    with factory() as db:
        admin = db.scalar(select(AdminAccount).where(AdminAccount.username == "hr-admin"))
        admin.session_version += 1
        db.commit()

    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_employee_registration_reserves_codes_and_writes_history_and_audit(app_client):
    client, factory = app_client
    token = csrf_from(client.get("/login").text)
    assert client.post("/login", data={
        "username": "hr-admin",
        "password": "HotelAdmin-Strong-2026!",
        "csrf_token": token,
    }).status_code == 303

    page = client.get("/employees")
    assert page.status_code == 200
    token = csrf_from(page.text)
    form = {
        "csrf_token": token,
        "full_name": "موظف الاستقبال",
        "national_id": "",
        "phone": "",
        "department_id": "1",
        "position": "موظف استقبال",
        "hire_date": "2026-11-01",
        "basic_salary": "5000.00",
        "daily_hours": "8",
        "fingerprint_id": "",
    }
    response = client.post("/employees", data=form)
    assert response.status_code == 303

    with factory() as db:
        employee = db.scalar(select(Employee))
        assert employee.code == "MY-0001"
        assert db.scalar(select(EmployeeChange).where(
            EmployeeChange.employee_id == employee.id
        )) is not None
        assert db.scalar(select(AuditLog).where(
            AuditLog.action == "employee_created"
        )) is not None
    # Code allocation advances independently of the employee record sequence.
    next_form = dict(form)
    next_form["csrf_token"] = csrf_from(client.get("/employees").text)
    response = client.post("/employees", data=next_form)
    assert response.status_code == 303
    with factory() as db:
        assert sorted(db.scalars(select(Employee.code)).all()) == ["MY-0001", "MY-0002"]


def test_logout_requires_csrf_and_clears_the_session(app_client):
    client, _ = app_client
    token = csrf_from(client.get("/login").text)
    client.post("/login", data={
        "username": "hr-admin",
        "password": "HotelAdmin-Strong-2026!",
        "csrf_token": token,
    })
    dashboard = client.get("/")
    logout_token = csrf_from(dashboard.text)

    rejected = client.post("/logout", data={"csrf_token": "wrong-token"})
    assert rejected.status_code == 303
    assert client.get("/").status_code == 200

    logout = client.post("/logout", data={"csrf_token": logout_token})
    assert logout.status_code == 303
    assert client.get("/").headers["location"] == "/login"



def test_admin_setup_is_one_time_and_never_sets_a_default_password():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as db:
            admin = create_admin("hr-admin", "HotelAdmin-Strong-2026!", db)
            assert verify_password("HotelAdmin-Strong-2026!", admin.password_hash)
            assert admin.password_hash != "HotelAdmin-Strong-2026!"
            with pytest.raises(RuntimeError, match="يوجد حساب مدير بالفعل"):
                create_admin("second-admin", "Another-Strong-Password-2026!", db)
            assert db.scalar(select(AdminAccount.id)) == admin.id
            audit_events = db.scalars(
                select(AuditLog).where(AuditLog.action == "administrator_created")
            ).all()
            assert len(audit_events) == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
