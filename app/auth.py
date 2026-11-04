"""Database-backed authentication for the single system administrator."""

from sqlalchemy.orm import Session

from app.models import AdminAccount
from app.security import verify_password


def authenticate(db: Session, username: str, password: str) -> AdminAccount | None:
    """Return an active administrator only when the supplied credentials match."""
    if not isinstance(username, str) or not isinstance(password, str):
        return None

    normalized_username = username.strip()
    if not normalized_username or not password:
        return None

    admin = db.query(AdminAccount).filter(AdminAccount.username == normalized_username).one_or_none()
    if admin is None or not admin.active:
        return None

    if not verify_password(password, admin.password_hash):
        return None

    return admin
