from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from app.db import Base
from app.models import (
    AdminAccount, AuditLog, Department, Employee,
    SecurityShiftAssignment, ShiftPattern,
)
from app.security_roster_workflow import (
    prepare_security_roster, confirm_security_roster_day,
)


@pytest.fixture
def roster_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        department = Department(name="Security", active=True)
        db.add(department)
        db.flush()
        employees = []
        for index, name in enumerate(("Fayez", "Yasser", "Walid"), start=1):
            employee = Employee(
                code=f"SEC-{index}", full_name=name, department_id=department.id,
                position="Security", hire_date=date(2024, 1, 1),
                basic_salary=Decimal("5000"), daily_hours=Decimal("8"),
                status="active",
            )
            db.add(employee)
            employees.append(employee)
        patterns = (
            ("NIGHT", time(0, 0), time(8, 0)),
            ("MORNING", time(8, 0), time(16, 0)),
            ("EVENING", time(16, 0), time(0, 0)),
        )
        for code, start, end in patterns:
            db.add(ShiftPattern(
                department_id=department.id, code=code, name=code,
                start_time=start, end_time=end, active=True,
                match_before_minutes=60, match_after_minutes=60,
            ))
        admin = AdminAccount(username="admin", password_hash="not-used", active=True)
        db.add(admin)
        db.commit()
        yield db, employees, admin
    engine.dispose()


def _parsed(employees, work_date=date(2026, 11, 1)):
    roster = {
        employees[0].id: time(0, 0),
        employees[1].id: time(8, 0),
        employees[2].id: time(16, 0),
    }
    return {
        "employee_headers": {employee.id: employee.full_name for employee in employees},
        "records": [{"work_date": work_date, "roster": roster}],
        "issues": [],
        "status": "parsed_for_review",
    }


def test_prepare_roster_does_not_persist_assignments(roster_db):
    db, employees, _admin = roster_db
    prepared = prepare_security_roster(db, _parsed(employees))
    assert prepared["days"][0]["can_confirm"] is True
    assert db.scalar(select(func.count()).select_from(SecurityShiftAssignment)) == 0
    assert db.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_confirm_roster_persists_all_three_assignments_and_audit(roster_db):
    db, employees, admin = roster_db
    parsed = _parsed(employees)
    prepared = prepare_security_roster(db, parsed)
    day = prepared["days"][0]
    confirm_security_roster_day(db, day["work_date"], day["roster"], admin, parsed["issues"])
    db.commit()
    assert db.scalar(select(func.count()).select_from(SecurityShiftAssignment)) == 3
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "security_roster_day_approved"))
    assert audit is not None
    assert audit.actor == "admin"


def test_import_issue_blocks_approval(roster_db):
    db, employees, admin = roster_db
    parsed = _parsed(employees)
    prepared = prepare_security_roster(db, parsed)
    with pytest.raises(ValueError, match="import_issues_block_day_approval"):
        confirm_security_roster_day(
            db, prepared["days"][0]["work_date"], prepared["days"][0]["roster"],
            admin, [{"date": "2026-11-01", "reason": "missing_or_invalid_shift_value"}],
        )
    assert db.scalar(select(func.count()).select_from(SecurityShiftAssignment)) == 0


def test_second_approval_for_same_day_is_rejected(roster_db):
    db, employees, admin = roster_db
    parsed = _parsed(employees)
    day = prepare_security_roster(db, parsed)["days"][0]
    confirm_security_roster_day(db, day["work_date"], day["roster"], admin, [])
    db.commit()
    with pytest.raises(ValueError, match="already_has_assignments"):
        confirm_security_roster_day(db, day["work_date"], day["roster"], admin, [])
    db.rollback()
    assert db.scalar(select(func.count()).select_from(SecurityShiftAssignment)) == 3


def test_inactive_security_pattern_is_not_used_for_proposal(roster_db):
    db, employees, _admin = roster_db
    evening = db.scalar(select(ShiftPattern).where(ShiftPattern.code == "EVENING"))
    evening.active = False
    db.commit()
    prepared = prepare_security_roster(db, _parsed(employees))
    assert prepared["days"][0]["can_confirm"] is False
    assert any("shift_pattern_resolution_missing" in issue
               for issue in prepared["days"][0]["proposal"]["issues"])


def test_matching_pattern_in_another_department_is_not_accepted(roster_db):
    db, employees, _admin = roster_db
    evening = db.scalar(select(ShiftPattern).where(ShiftPattern.code == "EVENING"))
    evening.active = False
    other_department = Department(name="Reception", active=True)
    db.add(other_department)
    db.flush()
    db.add(ShiftPattern(
        department_id=other_department.id, code="EVENING-OTHER", name="Wrong department",
        start_time=time(16, 0), end_time=time(0, 0), active=True,
        match_before_minutes=60, match_after_minutes=60,
    ))
    db.commit()
    prepared = prepare_security_roster(db, _parsed(employees))
    assert prepared["days"][0]["can_confirm"] is False


def test_forged_admin_identity_cannot_confirm_roster(roster_db):
    db, employees, admin = roster_db
    parsed = _parsed(employees)
    day = prepare_security_roster(db, parsed)["days"][0]
    forged_admin = AdminAccount(
        id=admin.id, username="forged-admin", password_hash="not-used", active=True
    )
    with pytest.raises(ValueError, match="authenticated_active_admin_required"):
        confirm_security_roster_day(db, day["work_date"], day["roster"], forged_admin, [])
    assert db.scalar(select(func.count()).select_from(SecurityShiftAssignment)) == 0


def test_audit_log_orm_update_is_rejected(roster_db):
    db, employees, admin = roster_db
    entry = AuditLog(
        actor="admin", action="test_event", entity_type="test",
        entity_id="1", reason="immutable test",
    )
    db.add(entry)
    db.commit()
    entry.reason = "tampered"
    with pytest.raises(ValueError, match="immutable"):
        db.flush()
    db.rollback()
    persisted = db.get(AuditLog, entry.id)
    assert persisted.reason == "immutable test"


def test_audit_log_orm_delete_is_rejected(roster_db):
    db, employees, admin = roster_db
    entry = AuditLog(
        actor="admin", action="test_event", entity_type="test",
        entity_id="2", reason="immutable test",
    )
    db.add(entry)
    db.commit()
    db.delete(entry)
    with pytest.raises(ValueError, match="cannot be deleted"):
        db.flush()
    db.rollback()
    assert db.get(AuditLog, entry.id) is not None
