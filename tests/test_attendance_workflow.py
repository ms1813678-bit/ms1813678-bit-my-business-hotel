from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pytest

from app.db import Base
from app.attendance_workflow import persist_attendance_import
from app.models import AttendanceImportBatch, AttendancePunch, AuditLog, Department, Employee


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        department = Department(name="Attendance Test", active=True)
        db.add(department)
        db.flush()
        employee = Employee(
            code="ATT-001", full_name="Test Employee",
            department_id=department.id, position="Test",
            hire_date=date(2024, 1, 1), status="active",
            basic_salary=Decimal("5000"), daily_hours=Decimal("8"),
        )
        db.add(employee)
        db.commit()
        yield db
    Base.metadata.drop_all(engine)
    engine.dispose()


def parsed_payload():
    return {
        "source_type": "fingerprint",
        "period_start": date(2026, 11, 1),
        "period_end": date(2026, 11, 1),
        "events": [{
            "employee_id": 1,
            "raw_employee_code": "ATT-001",
            "raw_employee_name": "Test Employee",
            "punch_at": datetime(2026, 11, 1, 7, 50),
            "raw_timestamp": "07:50",
            "event_type": "PUNCH",
            "source_sheet": "Att.log report",
            "source_row": 2,
            "event_index": 0,
        }, {
            "employee_id": 1,
            "raw_employee_code": "ATT-001",
            "raw_employee_name": "Test Employee",
            "punch_at": datetime(2026, 11, 1, 16, 9),
            "raw_timestamp": "07:50 16:09",
            "event_type": "PUNCH",
            "source_sheet": "Att.log report",
            "source_row": 2,
            "event_index": 1,
        }],
        "issues": [],
    }


def test_persists_raw_events_and_audit_log_without_approving_daily_attendance(db_session):
    batch = persist_attendance_import(
        db_session, file_bytes=b"synthetic-xlsx", filename="fingerprint.xlsx",
        parsed=parsed_payload(), actor="hr-admin",
    )
    db_session.commit()

    assert batch.status == "parsed"
    assert batch.row_count == 2
    assert db_session.scalar(select(func.count()).select_from(AttendancePunch)) == 2
    assert db_session.scalar(select(func.count()).select_from(AuditLog)) == 1
    assert db_session.scalar(select(func.count()).select_from(AttendanceImportBatch)) == 1


def test_import_with_issues_is_held_for_review(db_session):
    payload = parsed_payload()
    payload["issues"] = [{"reason": "unknown_employee", "row": 4}]
    batch = persist_attendance_import(
        db_session, file_bytes=b"another-xlsx", filename="fingerprint.xlsx",
        parsed=payload, actor="hr-admin",
    )
    assert batch.status == "needs_review"
    assert batch.issue_count == 1


def test_same_source_file_cannot_be_imported_twice(db_session):
    payload = parsed_payload()
    persist_attendance_import(
        db_session, file_bytes=b"same-file", filename="one.xlsx",
        parsed=payload, actor="hr-admin",
    )
    db_session.flush()
    with pytest.raises(ValueError, match="attendance_source_file_already_imported"):
        persist_attendance_import(
            db_session, file_bytes=b"same-file", filename="renamed.xlsx",
            parsed=payload, actor="hr-admin",
        )


def test_import_without_any_valid_date_is_not_persisted(db_session):
    payload = {
        "source_type": "fingerprint",
        "period_start": None,
        "period_end": None,
        "events": [],
        "issues": [{"reason": "invalid_or_missing_date"}],
    }
    with pytest.raises(ValueError, match="attendance_import_has_no_valid_dated_events"):
        persist_attendance_import(
            db_session, file_bytes=b"bad-file", filename="bad.xlsx",
            parsed=payload, actor="hr-admin",
        )
    assert db_session.scalar(select(func.count()).select_from(AttendanceImportBatch)) == 0
