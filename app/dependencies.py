"""FastAPI dependencies for validating the administrator session."""

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AdminAccount


def get_current_admin(
    request: Request,
    db: Session = Depends(get_db),
) -> AdminAccount:
    """Resolve the session username to an existing, active administrator account."""
    username = request.session.get("admin_username")
    if not isinstance(username, str) or not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="يجب تسجيل الدخول أولاً.",
            headers={"WWW-Authenticate": "Session"},
        )

    admin = (
        db.query(AdminAccount)
        .filter(AdminAccount.username == username)
        .one_or_none()
    )
    if admin is None or not admin.active:
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="جلسة المسؤول غير صالحة. يرجى تسجيل الدخول مرة أخرى.",
            headers={"WWW-Authenticate": "Session"},
        )

    return admin


# A descriptive alias for route declarations.
require_admin = get_current_admin
