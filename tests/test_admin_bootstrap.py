import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.db import Base
from app.models import AdminAccount
from app.security import verify_password
from scripts.create_admin import create_admin_account


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_bootstrap_creates_one_hashed_admin(db):
    admin = create_admin_account(db, " first-admin ", "Valid-Test-Password-123!")
    db.commit()
    assert admin.username == "first-admin"
    assert admin.singleton_key == 1 and admin.active
    assert verify_password("Valid-Test-Password-123!", admin.password_hash)


def test_bootstrap_refuses_second_admin(db):
    create_admin_account(db, "first-admin", "Valid-Test-Password-123!")
    db.commit()
    with pytest.raises(ValueError, match="already exists"):
        create_admin_account(db, "second-admin", "Another-Valid-Password-456!")


def test_database_constraint_rejects_second_admin_row(db):
    create_admin_account(db, "first-admin", "Valid-Test-Password-123!")
    db.commit()
    db.add(AdminAccount(singleton_key=1, username="second-admin", password_hash="fake", active=True))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_admin_cannot_be_deactivated_or_deleted(db):
    create_admin_account(db, "first-admin", "Valid-Test-Password-123!")
    db.commit()
    admin = db.scalar(select(AdminAccount))
    admin.active = False
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    admin = db.scalar(select(AdminAccount))
    db.delete(admin)
    with pytest.raises(ValueError, match="cannot be deleted"):
        db.flush()
