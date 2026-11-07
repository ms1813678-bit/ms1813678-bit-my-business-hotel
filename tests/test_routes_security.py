import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import AdminAccount, Department, Employee, EmployeeCodeCounter
from app.security import hash_password


@pytest.fixture()
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as db:
        admin = AdminAccount(
            username="integration-admin",
            password_hash=hash_password("Integration-Test-Password-123!"),
            active=True,
        )
        department = Department(name="Test Department", active=True)
        db.add_all([admin, department, EmployeeCodeCounter(id=1, last_number=0)])
        db.commit()
        db.refresh(admin)
        db.refresh(department)
        department_id = department.id
        admin_id = admin.id

    def override_get_db():
        with TestingSessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.main.SessionLocal", TestingSessionLocal)

    with TestClient(app) as test_client:
        test_client.test_department_id = department_id
        test_client.test_admin_id = admin_id
        yield test_client

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def csrf_from(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, "CSRF hidden field was not rendered"
    return match.group(1)


def login(client):
    page = client.get("/login")
    token = csrf_from(page)
    response = client.post(
        "/login",
        data={
            "username": "integration-admin",
            "password": "Integration-Test-Password-123!",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    return response


def test_anonymous_browser_is_redirected_to_login_for_protected_pages(client):
    for path in ("/", "/employees"):
        response = client.get(path, headers={"accept": "text/html"}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_api_request_keeps_401_for_unauthenticated_user(client):
    response = client.get("/", headers={"accept": "application/json"})
    assert response.status_code == 401
    assert response.json()["detail"]


def test_missing_session_cookie_redirects_browser_to_login(client):
    """A removed cookie tests an absent session, not natural cookie expiration."""
    assert login(client).status_code == 303
    # Removing the cookie tests an absent session, not expiration of a still-present cookie.
    client.cookies.delete("session")
    response = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_anonymous_user_cannot_open_protected_pages_or_post(client):
    assert client.get("/").status_code == 401
    assert client.get("/employees").status_code == 401
    assert client.post("/employees", data={}).status_code == 403
    anonymous_page = client.get("/login")
    anonymous_token = csrf_from(anonymous_page)
    assert client.post(
        "/employees",
        data={"csrf_token": anonymous_token},
    ).status_code == 401
    assert client.post("/logout", data={}).status_code == 403


@pytest.mark.parametrize("path", ["/login", "/logout", "/employees", "/attendance/months/1/approve"])
def test_post_routes_reject_missing_or_invalid_csrf(client, path):
    if path == "/logout":
        assert login(client).status_code == 303
    for token in ("", "invalid-csrf-token"):
        response = client.post(path, data={"csrf_token": token})
        assert response.status_code == 403


def test_login_and_dashboard_require_valid_credentials_and_csrf(client):
    page = client.get("/login")
    token = csrf_from(page)

    missing_token = client.post(
        "/login",
        data={"username": "integration-admin", "password": "Integration-Test-Password-123!"},
    )
    assert missing_token.status_code == 403

    bad_password = client.post(
        "/login",
        data={
            "username": "integration-admin",
            "password": "Wrong-Integration-Password-456!",
            "csrf_token": token,
        },
    )
    assert bad_password.status_code == 401
    assert "csrf_token" in bad_password.text

    success = login(client)
    assert success.status_code == 303
    assert success.headers["location"] == "/"
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "integration-admin" in dashboard.text


@pytest.mark.parametrize("change", ["deactivate", "delete"])
def test_sole_admin_cannot_be_deactivated_or_deleted(client, change):
    from sqlalchemy.exc import IntegrityError

    assert login(client).status_code == 303
    from app.main import SessionLocal

    with SessionLocal() as db:
        admin = db.get(AdminAccount, client.test_admin_id)
        if change == "deactivate":
            admin.active = False
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
        else:
            db.delete(admin)
            with pytest.raises(ValueError, match="cannot be deleted"):
                db.flush()
            db.rollback()

    # A failed destructive operation must not lock the only administrator out.
    response = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert response.status_code == 200
    assert "integration-admin" in response.text


def test_employee_creation_requires_auth_and_valid_csrf(client):
    assert login(client).status_code == 303
    page = client.get("/employees")
    assert page.status_code == 200
    token = csrf_from(page)

    data = {
        "csrf_token": token,
        "full_name": "Synthetic Test Employee",
        "national_id": "",
        "phone": "",
        "department_id": str(client.test_department_id),
        "position": "Test Position",
        "hire_date": date.today().isoformat(),
        "basic_salary": str(Decimal("1000.00")),
        "daily_hours": "8",
        "fingerprint_id": "",
    }
    rejected = client.post(
        "/employees",
        data={**data, "csrf_token": "wrong-integration-csrf-token"},
    )
    assert rejected.status_code == 403

    response = client.post("/employees", data=data, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/employees"

    from app.main import SessionLocal

    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(full_name="Synthetic Test Employee").one()
        assert employee.code == "MY-0001"


def test_attendance_review_page_is_paginated(client):
    assert login(client).status_code == 303
    from app.main import SessionLocal
    from app.models import AttendanceDailyRecord

    token = csrf_from(client.get("/employees"))
    created = client.post(
        "/employees",
        data={
            "csrf_token": token,
            "full_name": "Attendance Pagination Employee",
            "department_id": str(client.test_department_id),
            "position": "Test Position",
            "hire_date": "2025-01-01",
            "basic_salary": "1000.00",
            "daily_hours": "8",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(
            full_name="Attendance Pagination Employee"
        ).one()
        employee_id = employee.id
        for index in range(105):
            db.add(AttendanceDailyRecord(
                employee_id=employee_id,
                work_date=date(2026, 11, 1) + timedelta(days=index),
                status="needs_review",
            ))
        db.commit()

    first = client.get("/attendance/review")
    assert first.status_code == 200
    assert "عرض 100 سجلًا من إجمالي 105 سجلًا" in first.text
    assert "الصفحة 1 من 2" in first.text
    assert 'href="/attendance/review?page=2"' in first.text
    second = client.get("/attendance/review?page=2")
    assert second.status_code == 200
    assert "عرض 5 سجلًا من إجمالي 105 سجلًا" in second.text
    assert "الصفحة 2 من 2" in second.text


def test_logout_requires_csrf_and_clears_session(client):
    assert login(client).status_code == 303
    dashboard = client.get("/")
    token = csrf_from(dashboard)

    rejected = client.post("/logout", data={"csrf_token": "wrong-token"})
    assert rejected.status_code == 403

    response = client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/").status_code == 401


def _session_middleware_options():
    middleware = next(
        item for item in app.user_middleware if item.cls.__name__ == "SessionMiddleware"
    )
    return middleware.kwargs


def test_session_cookie_has_expected_lifetime_and_security_attributes(client):
    response = client.get("/login")
    cookie = response.headers["set-cookie"]
    options = _session_middleware_options()

    assert options["max_age"] == 8 * 60 * 60 == 28800
    assert "session=" in cookie
    assert "Max-Age=28800" in cookie
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    assert options["same_site"] == "lax"


def test_session_cookie_secure_flag_follows_production_environment(monkeypatch):
    import importlib
    import os

    from app import config as config_module
    from app import main as main_module

    original_app_env = os.environ.get("APP_ENV")
    monkeypatch.setenv("APP_ENV", "production")

    try:
        # Reload settings so Pydantic reads APP_ENV from the real process environment.
        production_config = importlib.reload(config_module)
        assert production_config.settings.app_env.lower() == "production"

        # Rebuild the actual application using the freshly loaded production settings.
        production_main = importlib.reload(main_module)
        assert production_main.settings is production_config.settings

        middleware = next(
            item
            for item in production_main.app.user_middleware
            if item.cls.__name__ == "SessionMiddleware"
        )
        assert middleware.kwargs["https_only"] is True

        with TestClient(production_main.app) as production_client:
            response = production_client.get("/login")

        production_cookie = response.headers["set-cookie"].lower()
        assert "secure" in production_cookie
        assert "httponly" in production_cookie
        assert "samesite=lax" in production_cookie
        assert "max-age=28800" in production_cookie
    finally:
        if original_app_env is None:
            monkeypatch.delenv("APP_ENV", raising=False)
        else:
            monkeypatch.setenv("APP_ENV", original_app_env)

        # Restore module-level settings and app construction for other tests.
        importlib.reload(config_module)
        importlib.reload(main_module)


def test_login_rotates_csrf_token_and_rejects_pre_login_token(client):
    anonymous_page = client.get("/login")
    old_token = csrf_from(anonymous_page)

    successful_login = client.post(
        "/login",
        data={
            "username": "integration-admin",
            "password": "Integration-Test-Password-123!",
            "csrf_token": old_token,
        },
        follow_redirects=False,
    )
    assert successful_login.status_code == 303

    dashboard = client.get("/")
    new_token = csrf_from(dashboard)
    assert new_token != old_token

    old_token_response = client.post("/logout", data={"csrf_token": old_token})
    assert old_token_response.status_code == 403

    new_token_response = client.post(
        "/logout",
        data={"csrf_token": new_token},
        follow_redirects=False,
    )
    assert new_token_response.status_code == 303
    assert new_token_response.headers["location"] == "/login"



def test_admin_session_is_invalidated_when_session_version_changes(client):
    assert login(client).status_code == 303
    from app.main import SessionLocal

    from app.auth import change_admin_password

    with SessionLocal.begin() as db:
        admin = db.get(AdminAccount, client.test_admin_id)
        change_admin_password(db, admin, "Changed-Integration-Password-789!")

    response = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_employee_code_counter_is_persistent_and_never_reuses_reserved_number(client):
    from app.employee_codes import allocate_employee_code
    from app.main import SessionLocal

    with SessionLocal.begin() as db:
        assert allocate_employee_code(db) == "MY-0001"
    with SessionLocal.begin() as db:
        assert allocate_employee_code(db) == "MY-0002"
    # A fresh database session observes the committed counter value.
    with SessionLocal.begin() as db:
        assert allocate_employee_code(db) == "MY-0003"



def test_employee_archive_and_rehire_preserve_service_history(client):
    assert login(client).status_code == 303
    page = client.get("/employees")
    token = csrf_from(page)
    employee_data = {
        "csrf_token": token,
        "full_name": "Service History Test",
        "national_id": "",
        "phone": "",
        "department_id": str(client.test_department_id),
        "position": "Test Position",
        "hire_date": "2025-01-10",
        "basic_salary": "1000.00",
        "daily_hours": "8",
        "fingerprint_id": "",
    }
    assert client.post("/employees", data=employee_data, follow_redirects=False).status_code == 303

    from app.main import SessionLocal
    from app.models import EmployeeServicePeriod

    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(full_name="Service History Test").one()
        employee_id = employee.id
        employee_code = employee.code

    archive = client.post(
        f"/employees/{employee_id}/archive",
        data={"csrf_token": token, "reason": "End of first service"},
        follow_redirects=False,
    )
    assert archive.status_code == 303

    rehire = client.post(
        f"/employees/{employee_id}/rehire",
        data={
            "csrf_token": token,
            "new_hire_date": (date.today() + timedelta(days=1)).isoformat(),
            "reason": "Rehired after separation",
        },
        follow_redirects=False,
    )
    assert rehire.status_code == 303

    with SessionLocal() as db:
        employee = db.get(Employee, employee_id)
        periods = (
            db.query(EmployeeServicePeriod)
            .filter_by(employee_id=employee_id)
            .order_by(EmployeeServicePeriod.id)
            .all()
        )
        assert employee.code == employee_code
        assert employee.status == "active"
        assert employee.hire_date == date.today() + timedelta(days=1)
        assert len(periods) == 2
        assert periods[0].end_date == date.today()
        assert periods[0].end_reason == "End of first service"
        assert periods[1].start_date == date.today() + timedelta(days=1)


def test_employee_profile_edit_records_effective_dated_history_and_audit(client):
    from app.main import SessionLocal
    from app.models import AuditLog, Branch, EmployeeChange

    assert login(client).status_code == 303
    with SessionLocal.begin() as db:
        branch = Branch(
            department_id=client.test_department_id,
            name="Test Branch",
            active=True,
        )
        db.add(branch)
        db.flush()
        branch_id = branch.id

    token = csrf_from(client.get("/employees"))
    create_data = {
        "csrf_token": token,
        "full_name": "Profile Edit Test",
        "national_id": "TEST-NID-1001",
        "phone": "01000000000",
        "address": "Old address",
        "department_id": str(client.test_department_id),
        "branch_id": str(branch_id),
        "position": "Old Position",
        "hire_date": "2025-01-01",
        "basic_salary": "1000.00",
        "daily_hours": "8",
        "fingerprint_id": "TEST-FP-1001",
    }
    created = client.post("/employees", data=create_data, follow_redirects=False)
    assert created.status_code == 303

    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(full_name="Profile Edit Test").one()
        employee_id = employee.id

    edit_data = {
        "csrf_token": token,
        "full_name": "Profile Edit Test Updated",
        "national_id": "TEST-NID-1001",
        "phone": "01111111111",
        "address": "New address",
        "department_id": str(client.test_department_id),
        "branch_id": str(branch_id),
        "position": "New Position",
        "basic_salary": "1250.50",
        "daily_hours": "7.5",
        "fingerprint_id": "TEST-FP-1001",
        "effective_date": "2025-02-01",
        "reason": "Approved employee data correction",
    }
    edited = client.post(
        f"/employees/{employee_id}/edit",
        data=edit_data,
        follow_redirects=False,
    )
    assert edited.status_code == 303

    with SessionLocal() as db:
        employee = db.get(Employee, employee_id)
        changes = (
            db.query(EmployeeChange)
            .filter_by(employee_id=employee_id)
            .all()
        )
        audit = (
            db.query(AuditLog)
            .filter_by(entity_type="employee", entity_id=str(employee_id), action="employee_updated")
            .one()
        )
        assert employee.full_name == "Profile Edit Test Updated"
        assert employee.address == "New address"
        assert employee.phone == "01111111111"
        assert employee.basic_salary == Decimal("1250.50")
        assert employee.daily_hours == Decimal("7.50")
        assert {change.field_name for change in changes} == {
            "full_name", "phone", "address", "position", "basic_salary", "daily_hours"
        }
        assert all(change.effective_date == date(2025, 2, 1) for change in changes)
        assert all(change.reason == "Approved employee data correction" for change in changes)
        assert all(change.old_value is not None and change.new_value is not None for change in changes)
        assert audit.actor == "integration-admin"
        assert "Approved employee data correction" == audit.reason


def test_employee_edit_rejects_branch_from_another_department(client):
    from app.main import SessionLocal
    from app.models import Branch

    assert login(client).status_code == 303
    with SessionLocal.begin() as db:
        other_department = Department(name="Other Test Department", active=True)
        db.add(other_department)
        db.flush()
        wrong_branch = Branch(
            department_id=other_department.id,
            name="Wrong Department Branch",
            active=True,
        )
        db.add(wrong_branch)
        db.flush()
        wrong_branch_id = wrong_branch.id

    token = csrf_from(client.get("/employees"))
    create_data = {
        "csrf_token": token,
        "full_name": "Branch Validation Test",
        "department_id": str(client.test_department_id),
        "position": "Test Position",
        "hire_date": date.today().isoformat(),
        "basic_salary": "1000.00",
        "daily_hours": "8",
    }
    assert client.post("/employees", data=create_data, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(full_name="Branch Validation Test").one()
        employee_id = employee.id

    response = client.post(
        f"/employees/{employee_id}/edit",
        data={
            "csrf_token": token,
            "full_name": "Branch Validation Test",
            "department_id": str(client.test_department_id),
            "branch_id": str(wrong_branch_id),
            "position": "Test Position",
            "basic_salary": "1000.00",
            "daily_hours": "8",
            "effective_date": date.today().isoformat(),
            "reason": "Testing branch ownership validation",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_department_branch_management_and_employee_transfer_history(client):
    from app.main import SessionLocal
    from app.models import AuditLog, Branch, EmployeeChange

    assert login(client).status_code == 303
    token = csrf_from(client.get("/employees"))

    created_department = client.post(
        "/departments",
        data={"csrf_token": token, "name": "Transfer Destination"},
        follow_redirects=False,
    )
    assert created_department.status_code == 303

    with SessionLocal() as db:
        destination = db.query(Department).filter_by(name="Transfer Destination").one()
        destination_id = destination.id

    created_branch = client.post(
        "/branches",
        data={"csrf_token": token, "department_id": destination_id, "name": "Destination Branch"},
        follow_redirects=False,
    )
    assert created_branch.status_code == 303

    with SessionLocal() as db:
        branch = db.query(Branch).filter_by(name="Destination Branch").one()
        branch_id = branch.id

    created_employee = client.post(
        "/employees",
        data={
            "csrf_token": token,
            "full_name": "Transfer History Test",
            "department_id": client.test_department_id,
            "position": "Test Position",
            "hire_date": "2025-01-01",
            "basic_salary": "1000.00",
            "daily_hours": "8",
        },
        follow_redirects=False,
    )
    assert created_employee.status_code == 303
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(full_name="Transfer History Test").one()
        employee_id = employee.id

    moved = client.post(
        f"/employees/{employee_id}/transfer",
        data={
            "csrf_token": token,
            "department_id": destination_id,
            "branch_id": branch_id,
            "effective_date": "2025-03-01",
            "reason": "Approved department transfer",
        },
        follow_redirects=False,
    )
    assert moved.status_code == 303

    with SessionLocal() as db:
        employee = db.get(Employee, employee_id)
        changes = db.query(EmployeeChange).filter_by(employee_id=employee_id).all()
        audit = db.query(AuditLog).filter_by(
            entity_type="employee",
            entity_id=str(employee_id),
            action="employee_transferred",
        ).one()
        assert employee.department_id == destination_id
        assert employee.branch_id == branch_id
        assert {item.field_name for item in changes} == {"department_id", "branch_id"}
        assert all(item.effective_date == date(2025, 3, 1) for item in changes)
        assert all(item.reason == "Approved department transfer" for item in changes)
        assert audit.reason == "Approved department transfer"


def test_branch_cannot_be_deactivated_while_active_employee_is_assigned(client):
    from app.main import SessionLocal
    from app.models import Branch

    assert login(client).status_code == 303
    with SessionLocal.begin() as db:
        branch = Branch(
            department_id=client.test_department_id,
            name="Occupied Branch",
            active=True,
        )
        db.add(branch)
        db.flush()
        branch_id = branch.id

    token = csrf_from(client.get("/employees"))
    created = client.post(
        "/employees",
        data={
            "csrf_token": token,
            "full_name": "Occupied Branch Employee",
            "department_id": client.test_department_id,
            "branch_id": branch_id,
            "position": "Test Position",
            "hire_date": date.today().isoformat(),
            "basic_salary": "1000.00",
            "daily_hours": "8",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303

    response = client.post(
        f"/branches/{branch_id}/edit",
        data={
            "csrf_token": token,
            "department_id": client.test_department_id,
            "name": "Occupied Branch",
        },
        follow_redirects=False,
    )
    assert response.status_code == 409


def test_employee_creation_audits_record_and_rejects_duplicate_identifiers(client):
    from app.main import SessionLocal
    from app.models import AuditLog

    assert login(client).status_code == 303
    token = csrf_from(client.get("/employees"))
    data = {
        "csrf_token": token,
        "full_name": "Unique Employee",
        "national_id": "UNIQUE-NID-2001",
        "phone": "01000000001",
        "address": "Hotel address",
        "department_id": str(client.test_department_id),
        "position": "Receptionist",
        "hire_date": "2025-01-01",
        "basic_salary": "5000.00",
        "daily_hours": "8",
        "fingerprint_id": "UNIQUE-FP-2001",
    }
    created = client.post("/employees", data=data, follow_redirects=False)
    assert created.status_code == 303
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(national_id="UNIQUE-NID-2001").one()
        employee_id = employee.id
        code = employee.code
        audit = db.query(AuditLog).filter_by(
            entity_type="employee", entity_id=str(employee_id), action="employee_created"
        ).one()
        assert audit.actor == "integration-admin"
        assert code == "MY-0001"

    duplicate = dict(data, full_name="Duplicate National ID", fingerprint_id="UNIQUE-FP-2002")
    response = client.post("/employees", data=duplicate, follow_redirects=False)
    assert response.status_code == 409

    duplicate_fp = dict(data, full_name="Duplicate Fingerprint", national_id="UNIQUE-NID-2002")
    response = client.post("/employees", data=duplicate_fp, follow_redirects=False)
    assert response.status_code == 409

    with SessionLocal() as db:
        assert db.query(Employee).count() == 1


def test_employee_probation_dates_are_optional_validated_and_audited(client):
    from app.main import SessionLocal
    from app.models import EmployeeChange

    assert login(client).status_code == 303
    token = csrf_from(client.get("/employees"))
    created = client.post(
        "/employees",
        data={
            "csrf_token": token,
            "full_name": "Probation Dates Test",
            "department_id": client.test_department_id,
            "position": "Test Position",
            "hire_date": "2025-01-01",
            "probation_start_date": "2025-01-01",
            "probation_end_date": "2025-03-31",
            "basic_salary": "1000.00",
            "daily_hours": "8",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(full_name="Probation Dates Test").one()
        employee_id = employee.id
        assert employee.probation_start_date == date(2025, 1, 1)
        assert employee.probation_end_date == date(2025, 3, 31)

    invalid = client.post(
        f"/employees/{employee_id}/edit",
        data={
            "csrf_token": token,
            "full_name": "Probation Dates Test",
            "department_id": client.test_department_id,
            "position": "Test Position",
            "basic_salary": "1000.00",
            "daily_hours": "8",
            "probation_start_date": "2025-04-01",
            "probation_end_date": "2025-03-01",
            "effective_date": "2025-04-01",
            "reason": "Invalid probation range",
        },
        follow_redirects=False,
    )
    assert invalid.status_code == 400

    updated = client.post(
        f"/employees/{employee_id}/edit",
        data={
            "csrf_token": token,
            "full_name": "Probation Dates Test",
            "department_id": client.test_department_id,
            "position": "Test Position",
            "basic_salary": "1000.00",
            "daily_hours": "8",
            "probation_start_date": "2025-01-01",
            "probation_end_date": "2025-04-30",
            "effective_date": "2025-04-01",
            "reason": "Probation period extended",
        },
        follow_redirects=False,
    )
    assert updated.status_code == 303
    with SessionLocal() as db:
        employee = db.get(Employee, employee_id)
        changes = db.query(EmployeeChange).filter_by(
            employee_id=employee_id,
            field_name="probation_end_date",
        ).all()
        assert employee.probation_end_date == date(2025, 4, 30)
        assert len(changes) == 1
        assert changes[0].effective_date == date(2025, 4, 1)
        assert changes[0].reason == "Probation period extended"


def test_employee_data_change_request_can_be_reviewed_and_applied(client):
    from app.main import SessionLocal
    from app.models import AuditLog, EmployeeChange, EmployeeDataChangeRequest

    assert login(client).status_code == 303
    token = csrf_from(client.get("/employees"))
    created = client.post(
        "/employees",
        data={
            "csrf_token": token,
            "full_name": "Change Request Employee",
            "department_id": client.test_department_id,
            "position": "Receptionist",
            "hire_date": "2025-01-01",
            "basic_salary": "1000.00",
            "daily_hours": "8",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(full_name="Change Request Employee").one()
        employee_id = employee.id

    submitted = client.post(
        f"/employees/{employee_id}/data-change-requests",
        data={
            "csrf_token": token,
            "field_name": "phone",
            "requested_value": "01012345678",
            "request_reason": "Employee requested phone correction",
        },
        follow_redirects=False,
    )
    assert submitted.status_code == 303
    with SessionLocal() as db:
        request_row = db.query(EmployeeDataChangeRequest).filter_by(employee_id=employee_id).one()
        request_id = request_row.id
        assert request_row.status == "pending"

    reviewed = client.post(
        f"/employee-data-change-requests/{request_id}/review",
        data={
            "csrf_token": token,
            "decision": "apply",
            "decision_reason": "Identity checked and approved",
            "effective_date": "2025-02-01",
        },
        follow_redirects=False,
    )
    assert reviewed.status_code == 303
    with SessionLocal() as db:
        employee = db.get(Employee, employee_id)
        request_row = db.get(EmployeeDataChangeRequest, request_id)
        change = db.query(EmployeeChange).filter_by(
            employee_id=employee_id, field_name="phone"
        ).one()
        audit = db.query(AuditLog).filter_by(
            entity_type="employee_data_change_request",
            entity_id=str(request_id),
            action="employee_data_change_applied",
        ).one()
        assert employee.phone == "01012345678"
        assert request_row.status == "applied"
        assert request_row.reviewed_by == "integration-admin"
        assert change.old_value is None
        assert change.new_value == "01012345678"
        assert change.effective_date == date(2025, 2, 1)
        assert audit.reason == "Identity checked and approved"

    repeated = client.post(
        f"/employee-data-change-requests/{request_id}/review",
        data={
            "csrf_token": token,
            "decision": "reject",
            "decision_reason": "Duplicate review",
        },
        follow_redirects=False,
    )
    assert repeated.status_code == 409


def test_employee_photo_upload_is_validated_and_served_only_to_admin(client, monkeypatch, tmp_path):
    import io
    from PIL import Image
    import app.main as main_module

    monkeypatch.setattr(main_module, "EMPLOYEE_PHOTO_DIR", tmp_path)
    # Photo content must remain private even when the URL is known.
    anonymous = client.get("/employees/1/photo")
    assert anonymous.status_code == 401
    assert login(client).status_code == 303
    token = csrf_from(client.get("/employees"))

    image_bytes = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(image_bytes, format="PNG")
    created = client.post(
        "/employees",
        data={
            "csrf_token": token,
            "full_name": "Photo Employee",
            "department_id": client.test_department_id,
            "position": "Test Position",
            "hire_date": "2025-01-01",
            "basic_salary": "1000.00",
            "daily_hours": "8",
        },
        files={"photo": ("employee.png", image_bytes.getvalue(), "image/png")},
        follow_redirects=False,
    )
    assert created.status_code == 303
    with main_module.SessionLocal() as db:
        employee = db.query(Employee).filter_by(full_name="Photo Employee").one()
        employee_id = employee.id
        photo_path = employee.photo_path
        assert photo_path.endswith(".png")
        assert (tmp_path / photo_path).is_file()

    served = client.get(f"/employees/{employee_id}/photo")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"

    invalid = client.post(
        "/employees",
        data={
            "csrf_token": token,
            "full_name": "Invalid Photo Employee",
            "department_id": client.test_department_id,
            "position": "Test Position",
            "hire_date": "2025-01-01",
            "basic_salary": "1000.00",
            "daily_hours": "8",
        },
        files={"photo": ("not-image.png", b"not an image", "image/png")},
        follow_redirects=False,
    )
    assert invalid.status_code == 400


def test_security_roster_upload_preview_and_explicit_day_approval(client, monkeypatch, tmp_path):
    from datetime import time
    from io import BytesIO
    from openpyxl import Workbook
    from sqlalchemy import func, select
    from app.models import AuditLog, SecurityShiftAssignment, ShiftPattern

    monkeypatch.setattr("app.main.SECURITY_ROSTER_STAGING_DIR", tmp_path / "staging")
    # The fixture's SessionLocal is replaced with its isolated SQLite database.
    from app.main import SessionLocal
    with SessionLocal() as db:
        department = Department(name="Security", active=True)
        db.add(department)
        db.flush()
        employees = []
        for index, name in enumerate(("Fayez", "Yasser", "Walid"), start=1):
            employee = Employee(
                code=f"SEC-{index}", full_name=name, department_id=department.id,
                position="Security", hire_date=date(2024, 1, 1),
                basic_salary=Decimal("5000"), daily_hours=Decimal("8"), status="active",
            )
            db.add(employee)
            employees.append(employee)
        for code, start, end in (
            ("MIDNIGHT", time(0, 0), time(8, 0)),
            ("MORNING", time(8, 0), time(16, 0)),
            ("EVENING", time(16, 0), time(0, 0)),
        ):
            db.add(ShiftPattern(
                department_id=department.id, code=code, name=code,
                start_time=start, end_time=end, active=True,
                match_before_minutes=60, match_after_minutes=60,
            ))
        db.commit()

    assert login(client).status_code == 303
    page = client.get("/security-roster")
    token = csrf_from(page)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Date", "Fayez", "Yasser", "Walid"])
    sheet.append([date(2026, 11, 1), "00:00", "08:00", "16:00"])
    sheet.append([date(2026, 11, 2), "00:00", "08:00", "16:00"])
    output = BytesIO()
    workbook.save(output)
    preview = client.post(
        "/security-roster/preview",
        data={"csrf_token": token},
        files={"file": ("security.xlsx", output.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert preview.status_code == 200
    assert "جاهز للاعتماد بعد المراجعة" in preview.text
    stage_match = re.search(r'name="stage_token" value="([^"]+)"', preview.text)
    assert stage_match

    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(SecurityShiftAssignment)) == 0
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 0

    approved = client.post(
        "/security-roster/confirm",
        data={
            "csrf_token": token,
            "work_date": "2026-11-01",
            "stage_token": stage_match.group(1),
            "reviewed": "yes",
        },
    )
    assert approved.status_code == 200
    assert "تم اعتماد جدول يوم 2026-11-01 بنجاح" in approved.text
    assert "تم اعتماد هذا اليوم بالفعل" in approved.text
    assert 'value="2026-11-02"' in approved.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(SecurityShiftAssignment)) == 3
        assert db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "security_roster_day_approved"
        )) == 1

    # The same monthly upload remains available for the next date.
    approved_second_day = client.post(
        "/security-roster/confirm",
        data={
            "csrf_token": token,
            "work_date": "2026-11-02",
            "stage_token": stage_match.group(1),
            "reviewed": "yes",
        },
    )
    assert approved_second_day.status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(SecurityShiftAssignment)) == 6
        assert db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "security_roster_day_approved"
        )) == 2



def test_security_roster_upload_preview_and_daily_confirmation(client):
    from datetime import time
    from io import BytesIO

    from openpyxl import Workbook
    from sqlalchemy import select

    from app.main import SessionLocal
    from app.models import AuditLog, SecurityShiftAssignment, ShiftPattern

    assert login(client).status_code == 303

    with SessionLocal.begin() as db:
        department = Department(name="Security", active=True)
        db.add(department)
        db.flush()
        employees = []
        for index, name in enumerate(("Fayez", "Yasser", "Walid"), start=1):
            employee = Employee(
                code=f"SEC-{index}",
                full_name=name,
                fingerprint_id=f"FP-{index}",
                department_id=department.id,
                position="Security",
                hire_date=date(2024, 1, 1),
                basic_salary=Decimal("5000"),
                daily_hours=Decimal("8"),
                status="active",
            )
            db.add(employee)
            employees.append(employee)
        for code, start, end in (
            ("NIGHT", time(0, 0), time(8, 0)),
            ("MORNING", time(8, 0), time(16, 0)),
            ("EVENING", time(16, 0), time(0, 0)),
        ):
            db.add(ShiftPattern(
                department_id=department.id,
                code=code,
                name=code,
                start_time=start,
                end_time=end,
                active=True,
                match_before_minutes=60,
                match_after_minutes=60,
            ))

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Date", "Fayez", "Yasser", "Walid"])
    sheet.append([date(2026, 11, 1), "00:00", "08:00", "16:00"])
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()

    page = client.get("/security-roster")
    csrf = csrf_from(page)
    preview = client.post(
        "/security-roster/preview",
        data={"csrf_token": csrf},
        files={
            "file": (
                "security.xlsx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert preview.status_code == 200
    assert "جاهز للاعتماد" in preview.text
    assert "stage_token" in preview.text

    token_match = re.search(r'name="stage_token" value="([^"]+)"', preview.text)
    assert token_match
    stage_token = token_match.group(1)
    assert client.get("/security-roster").status_code == 200

    rejected_without_review = client.post(
        "/security-roster/confirm",
        data={
            "csrf_token": csrf,
            "work_date": "2026-11-01",
            "stage_token": stage_token,
        },
    )
    assert rejected_without_review.status_code == 400

    confirmation = client.post(
        "/security-roster/confirm",
        data={
            "csrf_token": csrf,
            "work_date": "2026-11-01",
            "stage_token": stage_token,
            "reviewed": "yes",
        },
    )
    assert confirmation.status_code == 200
    assert "تم اعتماد جدول يوم 2026-11-01 بنجاح" in confirmation.text

    with SessionLocal() as db:
        assignments = db.scalars(
            select(SecurityShiftAssignment).where(
                SecurityShiftAssignment.work_date == date(2026, 11, 1)
            )
        ).all()
        assert len(assignments) == 3
        audit = db.scalar(select(AuditLog).where(
            AuditLog.action == "security_roster_day_approved"
        ))
        assert audit is not None
        assert audit.actor == "integration-admin"


def test_security_roster_expired_preview_is_rejected_and_deleted(monkeypatch, tmp_path):
    import os
    import time
    from starlette.requests import Request
    from fastapi import HTTPException
    from app import main as main_module

    staging = tmp_path / "staging"
    monkeypatch.setattr(main_module, "SECURITY_ROSTER_STAGING_DIR", staging)
    monkeypatch.setattr(main_module, "SECURITY_ROSTER_STAGING_TTL_SECONDS", 60)
    staging.mkdir()
    token = "expired-preview-token"
    path = staging / f"{token}.xlsx"
    path.write_bytes(b"synthetic workbook")
    old_time = time.time() - 120
    os.utime(path, (old_time, old_time))

    request_scope = {
        "type": "http", "method": "POST", "scheme": "http",
        "path": "/", "raw_path": b"/", "query_string": b"",
        "headers": [], "server": ("testserver", 80),
        "client": ("testclient", 50000), "http_version": "1.1",
        "session": {"security_roster_stage": token},
    }
    request = Request(request_scope)

    with pytest.raises(HTTPException) as exc:
        main_module._load_staged_security_roster(token, request)

    assert exc.value.status_code == 410
    assert not path.exists()


def test_security_roster_cleanup_removes_expired_files_without_new_upload(monkeypatch, tmp_path):
    import os
    from app import main as main_module

    staging = tmp_path / "staging"
    monkeypatch.setattr(main_module, "SECURITY_ROSTER_STAGING_DIR", staging)
    monkeypatch.setattr(main_module, "SECURITY_ROSTER_STAGING_TTL_SECONDS", 60)
    staging.mkdir()
    expired = staging / "expired.xlsx"
    fresh = staging / "fresh.xlsx"
    expired.write_bytes(b"old")
    fresh.write_bytes(b"new")
    os.utime(expired, (100, 100))
    os.utime(fresh, (950, 950))

    removed = main_module._cleanup_expired_security_roster_files(now=1000)

    assert removed == 1
    assert not expired.exists()
    assert fresh.exists()


def test_security_roster_preview_token_is_bound_to_its_own_session(monkeypatch, tmp_path):
    from starlette.requests import Request
    from fastapi import HTTPException
    from app import main as main_module

    staging = tmp_path / "staging"
    monkeypatch.setattr(main_module, "SECURITY_ROSTER_STAGING_DIR", staging)
    staging.mkdir()
    token = "admin-one-preview-token"
    (staging / f"{token}.xlsx").write_bytes(b"synthetic workbook")

    second_admin_request = Request({
        "type": "http", "method": "POST", "scheme": "http",
        "path": "/", "raw_path": b"/", "query_string": b"",
        "headers": [], "server": ("testserver", 80),
        "client": ("testclient", 50001), "http_version": "1.1",
        "session": {"admin_username": "admin-two", "security_roster_stage": "different-token"},
    })

    with pytest.raises(HTTPException) as exc:
        main_module._load_staged_security_roster(token, second_admin_request)

    assert exc.value.status_code == 403
    assert (staging / f"{token}.xlsx").exists()


def test_security_roster_preview_cannot_cross_admin_sessions_or_survive_logout(client, monkeypatch, tmp_path):
    from datetime import time
    from io import BytesIO
    from openpyxl import Workbook
    from fastapi.testclient import TestClient
    from app.main import SessionLocal
    from app.models import ShiftPattern

    monkeypatch.setattr("app.main.SECURITY_ROSTER_STAGING_DIR", tmp_path / "staging")
    with SessionLocal.begin() as db:
        security = Department(name="Security", active=True)
        db.add(security)
        db.flush()
        for index, name in enumerate(("Fayez", "Yasser", "Walid"), start=1):
            db.add(Employee(
                code=f"SEC-{index}", full_name=name, department_id=security.id,
                position="Security", hire_date=date(2024, 1, 1),
                basic_salary=Decimal("5000"), daily_hours=Decimal("8"), status="active",
            ))
        for code, start, end in (
            ("MIDNIGHT", time(0, 0), time(8, 0)),
            ("MORNING", time(8, 0), time(16, 0)),
            ("EVENING", time(16, 0), time(0, 0)),
        ):
            db.add(ShiftPattern(
                department_id=security.id, code=code, name=code,
                start_time=start, end_time=end, active=True,
                match_before_minutes=60, match_after_minutes=60,
            ))

    assert login(client).status_code == 303
    upload_page = client.get("/security-roster")
    first_csrf = csrf_from(upload_page)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Date", "Fayez", "Yasser", "Walid"])
    sheet.append([date(2026, 11, 1), "00:00", "08:00", "16:00"])
    output = BytesIO()
    workbook.save(output)
    preview = client.post(
        "/security-roster/preview",
        data={"csrf_token": first_csrf},
        files={"file": ("security.xlsx", output.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert preview.status_code == 200
    stage_match = re.search(r'name="stage_token" value="([^"]+)"', preview.text)
    assert stage_match
    stage_token = stage_match.group(1)

    with TestClient(app) as second_client:
        assert login(second_client).status_code == 303
        second_csrf = csrf_from(second_client.get("/security-roster"))
        cross_session = second_client.post(
            "/security-roster/confirm",
            data={
                "csrf_token": second_csrf,
                "work_date": "2026-11-01",
                "stage_token": stage_token,
                "reviewed": "yes",
            },
        )
        assert cross_session.status_code == 403

    staged_path = tmp_path / "staging" / f"{stage_token}.xlsx"
    assert staged_path.exists()
    logout = client.post(
        "/logout", data={"csrf_token": first_csrf}, follow_redirects=False
    )
    assert logout.status_code == 303
    assert not staged_path.exists()
    assert login(client).status_code == 303
    new_csrf = csrf_from(client.get("/security-roster"))
    after_logout = client.post(
        "/security-roster/confirm",
        data={
            "csrf_token": new_csrf,
            "work_date": "2026-11-01",
            "stage_token": stage_token,
            "reviewed": "yes",
        },
    )
    assert after_logout.status_code == 403


def test_attendance_import_route_saves_raw_evidence_but_not_daily_approval(client):
    from datetime import time
    from io import BytesIO
    from openpyxl import Workbook
    from app.main import SessionLocal
    from app.models import AttendanceDailyRecord, AttendanceImportBatch, AttendancePunch

    with SessionLocal.begin() as db:
        db.add(Employee(
            code="ATT-ROUTE-01", full_name="Attendance Test Employee",
            department_id=client.test_department_id, position="Test",
            hire_date=date(2024, 1, 1), status="active",
            basic_salary=Decimal("5000"), daily_hours=Decimal("8"),
            fingerprint_id="FP-ROUTE-01",
        ))

    assert login(client).status_code == 303
    page = client.get("/attendance/import")
    token = csrf_from(page)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["AC-No.", "Name", "Date", "Clock In", "Clock Out"])
    sheet.append(["ATT-ROUTE-01", "Attendance Test Employee", date(2026, 11, 1), time(8, 0), time(16, 0)])
    output = BytesIO()
    workbook.save(output)

    response = client.post(
        "/attendance/import",
        data={"csrf_token": token, "source_type": "program_report"},
        files={"file": ("program.xlsX", output.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200
    assert "تم تسجيل دفعة الاستيراد" in response.text
    with SessionLocal() as db:
        batch = db.query(AttendanceImportBatch).one()
        assert batch.original_file_bytes == output.getvalue()
        batch_id = batch.id
        assert db.query(AttendancePunch).count() == 2
        assert db.query(AttendanceDailyRecord).count() == 0

    download = client.get(f"/attendance/import/{batch_id}/download")
    assert download.status_code == 200
    assert download.content == output.getvalue()
    assert "attachment" in download.headers["content-disposition"]


def test_attendance_unknown_identity_can_be_resolved_only_by_authenticated_admin(client):
    from datetime import datetime
    from app.main import SessionLocal
    from app.models import (
        AttendanceImportBatch, AttendanceImportIssue, AttendancePunch, AuditLog,
    )

    with SessionLocal.begin() as db:
        employee = Employee(
            code="ATT-IDENTITY-01", full_name="Identity Target",
            department_id=client.test_department_id, position="Test",
            hire_date=date(2024, 1, 1), status="active",
            basic_salary=Decimal("5000"), daily_hours=Decimal("8"),
        )
        db.add(employee)
        db.flush()
        batch = AttendanceImportBatch(
            source_type="fingerprint", period_start=date(2026, 11, 1),
            period_end=date(2026, 11, 1), original_filename="fingerprint.xlsx",
            file_sha256="a" * 64, original_file_bytes=b"source-file",
            uploaded_by="integration-admin", status="needs_review",
            row_count=1, issue_count=1,
        )
        db.add(batch)
        db.flush()
        issue = AttendanceImportIssue(
            import_batch_id=batch.id, source_sheet="Att.log report",
            source_row=8, reason="unknown_employee", raw_value="FP-UNKNOWN",
        )
        db.add(issue)
        db.flush()
        db.add(AttendancePunch(
            import_batch_id=batch.id, employee_id=None,
            raw_employee_code="FP-UNKNOWN", raw_employee_name="Unknown",
            punch_at=datetime(2026, 11, 1, 7, 50), raw_timestamp="07:50",
            event_type="PUNCH", source_sheet="Att.log report",
            source_row=8, event_index=0,
        ))
        batch_id, issue_id, employee_id = batch.id, issue.id, employee.id

    assert login(client).status_code == 303
    page = client.get(f"/attendance/import/{batch_id}/issues")
    assert page.status_code == 200
    token = csrf_from(page)
    assert "FP-UNKNOWN" in page.text

    response = client.post(
        f"/attendance/import/{batch_id}/issues/{issue_id}/resolve",
        data={"csrf_token": token, "employee_id": str(employee_id)},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        batch = db.get(AttendanceImportBatch, batch_id)
        issue = db.get(AttendanceImportIssue, issue_id)
        punch = db.query(AttendancePunch).filter_by(import_batch_id=batch_id).one()
        assert batch.status == "parsed"
        assert batch.issue_count == 0
        assert issue.resolved_employee_id == employee_id
        assert punch.employee_id == employee_id
        assert punch.raw_employee_code == "FP-UNKNOWN"
        assert db.query(AuditLog).filter_by(
            action="attendance_import_unknown_employee_resolved"
        ).count() == 1


def test_attendance_month_page_displays_unrepresented_employee_day_gaps(client):
    from app.main import SessionLocal
    from app.models import AttendanceImportBatch, AttendanceMonth

    with SessionLocal.begin() as db:
        employee = Employee(
            code="ATT-COVERAGE-ROUTE", full_name="Coverage Route Employee",
            department_id=client.test_department_id, position="Test",
            hire_date=date(2024, 1, 1), status="active",
            basic_salary=Decimal("5000"), daily_hours=Decimal("8"),
        )
        db.add(employee)
        db.flush()
        program = AttendanceImportBatch(
            source_type="program_report", period_start=date(2026, 11, 1),
            period_end=date(2026, 11, 1), original_filename="program.xlsx",
            file_sha256="3" * 64, uploaded_by="integration-admin",
            status="parsed", row_count=0, issue_count=0,
        )
        fingerprint = AttendanceImportBatch(
            source_type="fingerprint", period_start=date(2026, 11, 1),
            period_end=date(2026, 11, 1), original_filename="fingerprint.xlsx",
            file_sha256="4" * 64, uploaded_by="integration-admin",
            status="parsed", row_count=0, issue_count=0,
        )
        db.add_all([program, fingerprint])
        db.flush()
        db.add(AttendanceMonth(
            period_start=date(2026, 11, 1), period_end=date(2026, 11, 1),
            program_import_batch_id=program.id,
            fingerprint_import_batch_id=fingerprint.id,
            status="under_review", created_by="integration-admin",
        ))

    assert login(client).status_code == 303
    response = client.get("/attendance/months")
    assert response.status_code == 200
    assert "أيام موظفين بلا سجل مطابقة: 1" in response.text
    assert "Coverage Route Employee" in response.text
    assert "لا يصنفها النظام غيابًا أو راحة تلقائيًا" in response.text


def test_attendance_import_route_requires_admin_and_csrf(client):
    assert client.get("/attendance/import").status_code == 401
    assert client.get("/attendance/import/1/download").status_code == 401
    assert client.get("/attendance/import/1/issues").status_code == 401
    response = client.post("/attendance/import", data={"source_type": "fingerprint"})
    assert response.status_code == 403
    resolution = client.post(
        "/attendance/import/1/issues/1/resolve", data={"employee_id": "1"}
    )
    assert resolution.status_code == 403


def test_monthly_attendance_reconciliation_page_is_admin_protected(client):
    assert client.get("/attendance/reconcile").status_code == 401
    assert login(client).status_code == 303
    page = client.get("/attendance/reconcile")
    assert page.status_code == 200
    assert "مطابقة الحضور الشهرية" in page.text
    assert client.post("/attendance/reconcile", data={}).status_code == 403


def test_attendance_review_page_is_admin_protected_and_renders_empty_state(client):
    assert client.get("/attendance/review").status_code == 401
    assert login(client).status_code == 303
    page = client.get("/attendance/review")
    assert page.status_code == 200
    assert "مراجعة واعتماد الحضور" in page.text
    assert "لا توجد سجلات معلقة" in page.text


def test_attendance_approval_endpoint_requires_csrf_and_admin(client):
    assert client.post(
        "/attendance/1/approve",
        data={"clock_in": "2026-11-01T08:00", "clock_out": "2026-11-01T16:00", "reason": "reviewed"},
    ).status_code == 403
    assert login(client).status_code == 303
    response = client.post(
        "/attendance/1/approve",
        data={"clock_in": "2026-11-01T08:00", "clock_out": "2026-11-01T16:00", "reason": "reviewed"},
    )
    assert response.status_code == 403


def test_overnight_attendance_review_prefills_clock_out_on_next_date(client):
    from datetime import datetime, time
    from app.main import SessionLocal
    from app.models import AttendanceDailyRecord, ShiftPattern

    with SessionLocal.begin() as db:
        employee = Employee(
            code="ATT-NIGHT-01", full_name="Night Shift Employee",
            department_id=client.test_department_id, position="Test",
            hire_date=date(2024, 1, 1), status="active",
            basic_salary=Decimal("5000"), daily_hours=Decimal("8"),
        )
        db.add(employee)
        db.flush()
        shift = ShiftPattern(
            department_id=client.test_department_id, code="NIGHT-TEST",
            name="Night Test", start_time=time(23, 0), end_time=time(7, 0),
            match_before_minutes=60, match_after_minutes=60, active=True,
        )
        db.add(shift)
        db.flush()
        db.add(AttendanceDailyRecord(
            employee_id=employee.id, work_date=date(2026, 11, 1),
            shift_pattern_id=shift.id,
            scheduled_start_at=datetime(2026, 11, 1, 23, 0),
            scheduled_end_at=datetime(2026, 11, 2, 7, 0),
            program_clock_in=datetime(2026, 11, 1, 23, 5),
            program_clock_out=datetime(2026, 11, 1, 7, 3),
            first_clock_in=datetime(2026, 11, 1, 23, 5),
            last_clock_out=datetime(2026, 11, 2, 7, 3),
            worked_minutes=478, status="ready_for_review",
        ))

    assert login(client).status_code == 303
    page = client.get("/attendance/review")
    assert page.status_code == 200
    assert 'value="2026-11-02T07:03"' in page.text

def test_employee_edit_rejects_effective_date_before_hire_date(client):
    from app.main import SessionLocal
    from app.models import EmployeeChange

    assert login(client).status_code == 303
    token = csrf_from(client.get("/employees"))
    created = client.post(
        "/employees",
        data={
            "csrf_token": token,
            "full_name": "Pre-Hire Edit Test",
            "department_id": client.test_department_id,
            "position": "Test Position",
            "hire_date": "2025-01-10",
            "basic_salary": "1000.00",
            "daily_hours": "8",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(full_name="Pre-Hire Edit Test").one()
        employee_id = employee.id

    rejected = client.post(
        f"/employees/{employee_id}/edit",
        data={
            "csrf_token": token,
            "full_name": "Pre-Hire Edit Test Updated",
            "department_id": client.test_department_id,
            "position": "Test Position",
            "basic_salary": "1000.00",
            "daily_hours": "8",
            "effective_date": "2025-01-09",
            "reason": "Must not predate employment",
        },
        follow_redirects=False,
    )
    assert rejected.status_code == 400
    with SessionLocal() as db:
        employee = db.get(Employee, employee_id)
        assert employee.full_name == "Pre-Hire Edit Test"
        assert db.query(EmployeeChange).filter_by(employee_id=employee_id).count() == 0


def test_employee_data_change_review_rejects_effective_date_before_hire_date(client):
    from app.main import SessionLocal
    from app.models import EmployeeDataChangeRequest

    assert login(client).status_code == 303
    token = csrf_from(client.get("/employees"))
    created = client.post(
        "/employees",
        data={
            "csrf_token": token,
            "full_name": "Pre-Hire Request Test",
            "department_id": client.test_department_id,
            "position": "Test Position",
            "hire_date": "2025-01-10",
            "basic_salary": "1000.00",
            "daily_hours": "8",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(full_name="Pre-Hire Request Test").one()
        employee_id = employee.id

    submitted = client.post(
        f"/employees/{employee_id}/data-change-requests",
        data={
            "csrf_token": token,
            "field_name": "phone",
            "requested_value": "01012345678",
            "request_reason": "Correct contact details",
        },
        follow_redirects=False,
    )
    assert submitted.status_code == 303
    with SessionLocal() as db:
        request_row = db.query(EmployeeDataChangeRequest).filter_by(employee_id=employee_id).one()
        request_id = request_row.id

    rejected = client.post(
        f"/employee-data-change-requests/{request_id}/review",
        data={
            "csrf_token": token,
            "decision": "apply",
            "decision_reason": "Reviewed",
            "effective_date": "2025-01-09",
        },
        follow_redirects=False,
    )
    assert rejected.status_code == 400
    with SessionLocal() as db:
        request_row = db.get(EmployeeDataChangeRequest, request_id)
        assert request_row.status == "pending"

def test_employee_photo_rejects_excessive_pixel_dimensions(client, monkeypatch, tmp_path):
    import io
    from PIL import Image
    import app.main as main_module

    monkeypatch.setattr(main_module, "EMPLOYEE_PHOTO_DIR", tmp_path)
    assert login(client).status_code == 303
    token = csrf_from(client.get("/employees"))

    image_bytes = io.BytesIO()
    Image.new("RGB", (4500, 4500), "white").save(image_bytes, format="PNG")
    response = client.post(
        "/employees",
        data={
            "csrf_token": token,
            "full_name": "Oversized Pixel Photo",
            "department_id": client.test_department_id,
            "position": "Test Position",
            "hire_date": "2025-01-01",
            "basic_salary": "1000.00",
            "daily_hours": "8",
        },
        files={"photo": ("large-dimensions.png", image_bytes.getvalue(), "image/png")},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert list(tmp_path.iterdir()) == []

