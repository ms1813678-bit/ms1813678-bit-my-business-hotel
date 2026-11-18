"""Password hashing and CSRF helpers for the single-administrator local application."""
import secrets

from pwdlib import PasswordHash
from starlette.requests import Request

_password_hash = PasswordHash.recommended()
_CSRF_SESSION_KEY = "_csrf_token"


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("كلمة المرور يجب ألا تقل عن 12 حرفًا.")
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hash.verify(password, password_hash)


def csrf_token_for(request: Request) -> str:
    token = request.session.get(_CSRF_SESSION_KEY)
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        request.session[_CSRF_SESSION_KEY] = token
    return token


def verify_csrf_token(request: Request, supplied: str | None) -> bool:
    expected = request.session.get(_CSRF_SESSION_KEY)
    return (
        isinstance(expected, str)
        and isinstance(supplied, str)
        and secrets.compare_digest(expected, supplied)
    )


def rotate_csrf_token(request: Request) -> str:
    token = secrets.token_urlsafe(32)
    request.session[_CSRF_SESSION_KEY] = token
    return token
