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


def test_sqlite_database_triggers_reject_direct_audit_mutations():
    import runpy
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    engine = create_engine("sqlite:///:memory:")
    try:
        AuditLog.__table__.create(engine)
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "alembic" / "versions" / "0009_audit_log_immutability.py"
        )
        upgrade = runpy.run_path(str(migration_path))["upgrade"]
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                upgrade()
            connection.execute(text("""
                INSERT INTO audit_logs
                    (actor, action, entity_type, entity_id, reason, created_at)
                VALUES
                    ('admin', 'test', 'test', 'sqlite-1', 'original', CURRENT_TIMESTAMP)
            """))
            with pytest.raises(DBAPIError):
                connection.execute(text(
                    "UPDATE audit_logs SET reason = 'tampered' WHERE entity_id = 'sqlite-1'"
                ))
            with pytest.raises(DBAPIError):
                connection.execute(text(
                    "DELETE FROM audit_logs WHERE entity_id = 'sqlite-1'"
                ))
            assert connection.execute(text(
                "SELECT reason FROM audit_logs WHERE entity_id = 'sqlite-1'"
            )).scalar_one() == "original"
    finally:
        engine.dispose()
