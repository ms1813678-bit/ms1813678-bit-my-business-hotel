from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.auth import authenticate


TEST_PASSWORD = "Test-Only-Password-123!"
WRONG_TEST_PASSWORD = "Wrong-Test-Password-456!"


def make_db(admin):
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = admin
    return db


def test_authenticate_returns_active_admin_for_valid_credentials(monkeypatch):
    admin = SimpleNamespace(username="test-admin", password_hash="fake-test-hash", active=True)
    db = make_db(admin)
    monkeypatch.setattr("app.auth.verify_password", lambda password, stored_hash: (
        password == TEST_PASSWORD and stored_hash == "fake-test-hash"
    ))

    assert authenticate(db, " test-admin ", TEST_PASSWORD) is admin


def test_authenticate_rejects_wrong_password(monkeypatch):
    admin = SimpleNamespace(username="test-admin", password_hash="fake-test-hash", active=True)
    db = make_db(admin)
    monkeypatch.setattr("app.auth.verify_password", lambda password, stored_hash: False)

    assert authenticate(db, "test-admin", WRONG_TEST_PASSWORD) is None


def test_authenticate_rejects_inactive_admin(monkeypatch):
    admin = SimpleNamespace(username="test-admin", password_hash="fake-test-hash", active=False)
    db = make_db(admin)
    verify = MagicMock(return_value=True)
    monkeypatch.setattr("app.auth.verify_password", verify)

    assert authenticate(db, "test-admin", TEST_PASSWORD) is None
    verify.assert_not_called()


def test_authenticate_rejects_unknown_admin():
    assert authenticate(make_db(None), "missing-test-admin", TEST_PASSWORD) is None


@pytest.mark.parametrize(("username", "password"), [("", TEST_PASSWORD), ("test-admin", ""), (None, TEST_PASSWORD)])
def test_authenticate_rejects_empty_or_invalid_credentials(username, password):
    db = MagicMock()
    assert authenticate(db, username, password) is None
    db.query.assert_not_called()
