import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.csrf import (
    CSRF_FORM_FIELD,
    CSRF_SESSION_KEY,
    csrf_protect,
    get_csrf_token,
    validate_csrf_token,
)


def make_request(session, form_data=None):
    async def form():
        return form_data or {}

    return SimpleNamespace(session=session, form=form)


def test_get_csrf_token_creates_and_reuses_session_token():
    request = make_request({})

    first = get_csrf_token(request)
    second = get_csrf_token(request)

    assert isinstance(first, str)
    assert len(first) >= 32
    assert first == second
    assert request.session[CSRF_SESSION_KEY] == first


def test_validate_csrf_token_accepts_matching_token():
    token = "test-csrf-token-value"
    validate_csrf_token(make_request({CSRF_SESSION_KEY: token}), token)


@pytest.mark.parametrize("submitted_token", [None, "", "wrong-test-csrf-token"])
def test_validate_csrf_token_rejects_missing_or_wrong_token(submitted_token):
    request = make_request({CSRF_SESSION_KEY: "expected-test-csrf-token"})

    with pytest.raises(HTTPException) as exc:
        validate_csrf_token(request, submitted_token)

    assert exc.value.status_code == 403


def test_validate_csrf_token_rejects_missing_session_token():
    with pytest.raises(HTTPException) as exc:
        validate_csrf_token(make_request({}), "test-csrf-token")

    assert exc.value.status_code == 403


def test_csrf_dependency_reads_form_field():
    token = "test-csrf-form-token"
    request = make_request(
        {CSRF_SESSION_KEY: token},
        {CSRF_FORM_FIELD: token},
    )

    asyncio.run(csrf_protect(request))


@pytest.mark.parametrize(
    "form_data",
    [
        {},
        {CSRF_FORM_FIELD: "wrong-test-csrf-token"},
    ],
    ids=["missing-token", "wrong-token"],
)
def test_csrf_protect_rejects_missing_or_wrong_form_token(form_data):
    request = make_request(
        {CSRF_SESSION_KEY: "expected-test-csrf-token"},
        form_data,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(csrf_protect(request))

    assert exc.value.status_code == 403
