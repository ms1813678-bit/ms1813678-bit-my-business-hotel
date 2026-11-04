import re
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import AdminAccount, Department, Employee
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
        db.add_all([admin, department])
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


@pytest.mark.parametrize("path", ["/login", "/logout", "/employees"])
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
def test_inactive_or_deleted_admin_invalidates_existing_session(client, change):
    assert login(client).status_code == 303

    # Change the account in the same isolated test database behind the active session.
    from app.main import SessionLocal

    with SessionLocal() as db:
        admin = db.get(AdminAccount, client.test_admin_id)
        if change == "deactivate":
            admin.active = False
        else:
            db.delete(admin)
        db.commit()

    invalidated_account_response = client.get(
        "/", headers={"accept": "text/html"}, follow_redirects=False
    )
    assert invalidated_account_response.status_code == 303
    assert invalidated_account_response.headers["location"] == "/login"
    # get_current_admin clears the invalid session rather than preserving it.
    assert client.get("/", headers={"accept": "text/html"}, follow_redirects=False).status_code == 303


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
