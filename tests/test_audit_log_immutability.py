from sqlalchemy import create_engine
from sqlalchemy.orm import Session
import pytest

from app.db import Base
from app.models import AuditLog


@pytest.fixture
def audit_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[AuditLog.__table__])
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_audit_log_orm_update_is_rejected(audit_session):
    entry = AuditLog(
        actor="admin",
        action="test",
        entity_type="test",
        entity_id="1",
        new_value="before",
    )
    audit_session.add(entry)
    audit_session.commit()

    entry.new_value = "after"
    with pytest.raises(ValueError, match="Audit log records are immutable"):
        audit_session.commit()
    audit_session.rollback()


def test_audit_log_orm_delete_is_rejected(audit_session):
    entry = AuditLog(
        actor="admin",
        action="test",
        entity_type="test",
        entity_id="2",
    )
    audit_session.add(entry)
    audit_session.commit()

    audit_session.delete(entry)
    with pytest.raises(ValueError, match="Audit log records cannot be deleted"):
        audit_session.commit()
    audit_session.rollback()
