"""Backup administration routes require the single HR administrator and CSRF."""
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import AdminAccount, BackupRun
from app.security import hash_password


@pytest.fixture()
def backup_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    with factory() as db:
        db.add(AdminAccount(
            username="backup-admin",
            password_hash=hash_password("Backup-Test-Password-123!"),
            active=True,
        ))
        db.commit()

    def override_get_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.main.SessionLocal", factory)
    try:
        with TestClient(app) as client:
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
            "username": "backup-admin",
            "password": "Backup-Test-Password-123!",
            "csrf_token": csrf(page),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_backup_page_and_mutations_are_admin_and_csrf_protected(backup_client):
    client = backup_client
    assert client.get("/settings/backup").status_code == 401
    assert client.post("/settings/backup/create", data={}).status_code == 403
    login(client)
    page = client.get("/settings/backup")
    assert page.status_code == 200
    assert "40 يومًا" in page.text
    assert "365 يومًا" in page.text
    assert "لا تُحذف تلقائيًا" in page.text


def test_manual_backup_without_external_drive_is_recorded_as_failed(backup_client):
    client = backup_client
    login(client)
    token = csrf(client.get("/settings/backup"))
    response = client.post(
        "/settings/backup/create",
        data={"csrf_token": token, "backup_type": "manual"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=backup" in response.headers["location"]
    with client.test_session_factory() as db:
        run = db.scalar(select(BackupRun))
        assert run.status == "failed"
        assert "external backup drive" in run.error_message


def test_restore_requires_explicit_phrase_and_reason(backup_client):
    client = backup_client
    login(client)
    token = csrf(client.get("/settings/backup"))
    response = client.post(
        "/settings/backup/restore",
        data={
            "csrf_token": token,
            "backup_id": "1",
            "confirm_text": "WRONG",
            "reason": "Synthetic restore validation",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=confirm" in response.headers["location"]
