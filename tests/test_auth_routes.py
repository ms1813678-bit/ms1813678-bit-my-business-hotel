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



def _login_as_admin(client):
    token = csrf_from(client.get("/login").text)
    response = client.post("/login", data={
        "username": "hr-admin",
        "password": "HotelAdmin-Strong-2026!",
        "csrf_token": token,
    })
    assert response.status_code == 303


def _create_test_employee(client):
    token = csrf_from(client.get("/employees").text)
    response = client.post("/employees", data={
        "csrf_token": token,
        "full_name": "موظف اختبار دورة الحياة",
        "national_id": "",
        "phone": "",
        "department_id": "1",
        "position": "موظف",
        "hire_date": "2026-11-01",
        "basic_salary": "5000.00",
        "daily_hours": "8",
        "fingerprint_id": "",
    })
    assert response.status_code == 303


def test_employee_profile_correction_archive_and_rehire_keep_service_history(app_client):
    client, factory = app_client
    _login_as_admin(client)
    _create_test_employee(client)

    with factory() as db:
        employee = db.scalar(select(Employee))
        employee_id = employee.id
        employee_code = employee.code
        period = db.scalar(select(EmployeeServicePeriod).where(
            EmployeeServicePeriod.employee_id == employee_id
        ))
        assert period.start_date.isoformat() == "2026-11-01"

    edit_page = client.get(f"/employees/{employee_id}/edit")
    assert edit_page.status_code == 200
    token = csrf_from(edit_page.text)
    corrected = client.post(f"/employees/{employee_id}/edit", data={
        "csrf_token": token,
        "full_name": "موظف اختبار دورة الحياة",
        "national_id": "",
        "phone": "",
        "address": "",
        "department_id": "1",
        "branch_id": "0",
        "position": "موظف",
        "hire_date": "2026-10-25",
        "basic_salary": "5000.00",
        "daily_hours": "8",
        "fingerprint_id": "",
        "effective_date": "2026-10-25",
        "reason": "تصحيح تاريخ التعيين",
    })
    assert corrected.status_code == 303

    with factory() as db:
        employee = db.get(Employee, employee_id)
        period = db.scalar(select(EmployeeServicePeriod).where(
            EmployeeServicePeriod.employee_id == employee_id
        ))
        assert employee.hire_date.isoformat() == "2026-10-25"
        assert period.start_date.isoformat() == "2026-10-25"
        correction = db.scalar(select(EmployeeChange).where(
            EmployeeChange.employee_id == employee_id,
            EmployeeChange.field_name == "hire_date",
        ))
        assert correction.old_value == "2026-11-01"
        assert correction.new_value == "2026-10-25"

    token = csrf_from(client.get(f"/employees/{employee_id}/edit").text)
    archived = client.post(f"/employees/{employee_id}/archive", data={
        "csrf_token": token,
        "effective_date": "2026-11-30",
        "reason": "انتهاء فترة العمل",
    })
    assert archived.status_code == 303
    with factory() as db:
        employee = db.get(Employee, employee_id)
        period = db.scalar(select(EmployeeServicePeriod).where(
            EmployeeServicePeriod.employee_id == employee_id
        ))
        assert employee.status == "terminated"
        assert period.end_date.isoformat() == "2026-11-30"
        assert period.end_reason == "انتهاء فترة العمل"

    token = csrf_from(client.get(f"/employees/{employee_id}/edit").text)
    rehired = client.post(f"/employees/{employee_id}/rehire", data={
        "csrf_token": token,
        "start_date": "2026-12-01",
        "reason": "إعادة تعيين",
    })
    assert rehired.status_code == 303
    with factory() as db:
        employee = db.get(Employee, employee_id)
        periods = db.scalars(select(EmployeeServicePeriod).where(
            EmployeeServicePeriod.employee_id == employee_id
        ).order_by(EmployeeServicePeriod.start_date)
        ).all()
        assert employee.status == "active"
        assert employee.code == employee_code
        assert len(periods) == 2
        assert periods[0].end_date.isoformat() == "2026-11-30"
        assert periods[1].start_date.isoformat() == "2026-12-01"
        assert periods[1].end_date is None
        assert db.scalar(select(AuditLog).where(
            AuditLog.action == "employee_archived",
            AuditLog.entity_id == str(employee_id),
        )) is not None
        assert db.scalar(select(AuditLog).where(
            AuditLog.action == "employee_rehired",
            AuditLog.entity_id == str(employee_id),
        )) is not None
