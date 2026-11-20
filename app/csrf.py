"""Session-bound CSRF token helpers and a FastAPI form dependency."""

import secrets

from fastapi import HTTPException, Request, status

CSRF_SESSION_KEY = "_csrf_token"
CSRF_FORM_FIELD = "csrf_token"


def get_csrf_token(request: Request) -> str:
    """Return the current session token, creating one when necessary."""
    token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf_token(request: Request, submitted_token: str | None) -> None:
    """Reject missing or mismatched form tokens using a constant-time comparison."""
    expected_token = request.session.get(CSRF_SESSION_KEY)
    if (
        not isinstance(expected_token, str)
        or not expected_token
        or not isinstance(submitted_token, str)
        or not submitted_token
        or not secrets.compare_digest(expected_token, submitted_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="انتهت صلاحية النموذج أو رمز الحماية غير صحيح. أعد تحميل الصفحة.",
        )


async def csrf_protect(request: Request) -> None:
    """FastAPI dependency for unsafe form routes; expects a csrf_token form field."""
    form = await request.form()
    validate_csrf_token(request, form.get(CSRF_FORM_FIELD))
