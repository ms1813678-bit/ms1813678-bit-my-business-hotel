from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.dependencies import get_current_admin


def make_db(admin):
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = admin
    return db


def make_request(session):
    return SimpleNamespace(session=session)


def test_get_current_admin_returns_active_account():
    admin = SimpleNamespace(
        username="test-admin",
        active=True,
        session_version=0,
    )
    request = make_request({
        "admin_username": " test-admin ",
        "admin_session_version": 0,
    })

    assert get_current_admin(request, make_db(admin)) is admin


def test_get_current_admin_rejects_missing_session():
    request = make_request({})

    with pytest.raises(HTTPException) as exc:
        get_current_admin(request, make_db(None))

    assert exc.value.status_code == 401


@pytest.mark.parametrize("admin", [None, SimpleNamespace(username="test-admin", active=False)])
def test_get_current_admin_rejects_invalid_or_inactive_account(admin):
    session = {"admin_username": "test-admin", "other": "test-value"}
    request = make_request(session)

    with pytest.raises(HTTPException) as exc:
        get_current_admin(request, make_db(admin))

    assert exc.value.status_code == 401
    assert session == {}
