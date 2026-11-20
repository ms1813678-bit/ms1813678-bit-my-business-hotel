"""Create the single administrator from a local terminal; no public signup route exists."""
from getpass import getpass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import AdminAccount
from app.security import hash_password


def create_admin_account(db: Session, username: str, password: str) -> AdminAccount:
    username = username.strip()
    if not username or len(username) > 80:
        raise ValueError("Username must contain between 1 and 80 characters.")
    if db.scalar(select(AdminAccount.id).limit(1)) is not None:
        raise ValueError("An administrator already exists; a second administrator is forbidden.")
    admin = AdminAccount(singleton_key=1, username=username, password_hash=hash_password(password), active=True)
    db.add(admin)
    db.flush()  # Unique DB constraint arbitrates concurrent setup attempts.
    return admin


def main() -> None:
    username = input("Administrator username: ").strip()
    password = getpass("Administrator password (minimum 12 characters): ")
    if password != getpass("Confirm administrator password: "):
        raise SystemExit("Passwords do not match.")
    try:
        with SessionLocal.begin() as db:
            create_admin_account(db, username, password)
    except (ValueError, IntegrityError) as exc:
        raise SystemExit("Administrator setup failed: " + str(exc)) from exc
    print("The single administrator account was created.")


if __name__ == "__main__":
    main()
