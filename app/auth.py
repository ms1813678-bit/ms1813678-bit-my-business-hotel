"""Administrator authentication and persistent session validation."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AdminAccount
from app.security import verify_password


def authenticate(db: Session, username: str, password: str) -> AdminAccount | None:
    """Return the active administrator only when the supplied password verifies."""
    admin = db.scalar(
        select(AdminAccount).where(
            AdminAccount.username == username,
            AdminAccount.active.is_(True),
        )
    )
    if admin is None:
        return None
    try:
        if not verify_password(password, admin.password_hash):
            return None
    except Exception:
        # A malformed stored hash must fail closed rather than break the login page.
        return None
    return admin


def get_session_admin(
    db: Session, *, admin_id: int, session_version: int
) -> AdminAccount | None:
    """Validate the signed cookie against the current active database account."""
    admin = db.get(AdminAccount, admin_id)
    if admin is None or not admin.active or admin.session_version != session_version:
        return None
    return admin
