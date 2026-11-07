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


def test_persistence_conflict_rolls_back_entire_day(roster_db, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    db, employees, admin = roster_db
    parsed = _parsed(employees)
    day = prepare_security_roster(db, parsed)["days"][0]
    # Load expired identity fields before injecting the simulated flush failure.
    _admin_id, _admin_username = admin.id, admin.username
    approved_admin = AdminAccount(
        id=_admin_id, username=_admin_username, password_hash="not-used", active=True
    )
    original_flush = db.flush

    def conflicting_flush(*args, **kwargs):
        if db.new:
            raise IntegrityError("INSERT", {}, Exception("unique constraint conflict"))
        return original_flush(*args, **kwargs)

    monkeypatch.setattr(db, "flush", conflicting_flush)
    with pytest.raises(ValueError, match="approval_conflict_or_persistence_failure"):
        confirm_security_roster_day(db, day["work_date"], day["roster"], approved_admin, [])
    monkeypatch.setattr(db, "flush", original_flush)
    assert db.scalar(select(func.count()).select_from(SecurityShiftAssignment)) == 0
    assert db.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_four_person_team_uses_unique_configured_patterns_and_detects_overlap(roster_db):
    db, employees, _admin = roster_db
    department = db.scalar(select(Department).where(Department.name == "Security"))
    fourth = Employee(
        code="SEC-04", full_name="Hany", department_id=department.id,
        position="Security", hire_date=date(2024, 1, 1),
        basic_salary=Decimal("5000"), daily_hours=Decimal("8"), status="active",
    )
    db.add(fourth)
    db.flush()
    for code, start, end in (
        ("TWO", time(2, 0), time(6, 0)),
        ("SIX", time(6, 0), time(14, 0)),
        ("FOURTEEN", time(14, 0), time(20, 0)),
        ("TWENTY", time(20, 0), time(2, 0)),
        ("FOUR_OVERLAP", time(4, 0), time(10, 0)),
    ):
        db.add(ShiftPattern(
            department_id=department.id, code=code, name=code,
            start_time=start, end_time=end, active=True,
            match_before_minutes=60, match_after_minutes=60,
        ))
    db.flush()
    roster = {
        employees[0].id: time(2, 0),
        employees[1].id: time(6, 0),
        employees[2].id: time(14, 0),
        fourth.id: time(20, 0),
    }
    parsed = {
        "employee_headers": {
            employee.id: employee.full_name
            for employee in [*employees, fourth]
        },
        "records": [{"work_date": date(2026, 11, 2), "roster": roster}],
        "issues": [],
        "status": "parsed_for_review",
    }
    prepared = prepare_security_roster(db, parsed)
    assert prepared["days"][0]["can_confirm"] is True
    assert len(prepared["days"][0]["proposal"]["assignments"]) == 4

    overlapping = dict(roster)
    overlapping[employees[1].id] = time(4, 0)
    from app.security_roster import build_security_day_proposal
    patterns = [
        {"id": pattern.id, "start_time": pattern.start_time, "end_time": pattern.end_time}
        for pattern in db.scalars(select(ShiftPattern).where(
            ShiftPattern.department_id == department.id,
            ShiftPattern.active.is_(True),
        )).all()
    ]
    proposal = build_security_day_proposal(overlapping, patterns)
    assert proposal["status"] == "needs_review"
    assert any(
        issue.startswith("overlapping_security_shifts:")
        for issue in proposal["issues"]
    )


def test_two_person_team_is_supported(roster_db):
    db, employees, admin = roster_db
    employees[2].status = "inactive"
    department = db.scalar(select(Department).where(Department.name == "Security"))
    db.add_all([
        ShiftPattern(
            department_id=department.id, code="TWO_A", name="Two A",
            start_time=time(8, 0), end_time=time(20, 0), active=True,
            match_before_minutes=60, match_after_minutes=60,
        ),
        ShiftPattern(
            department_id=department.id, code="TWO_B", name="Two B",
            start_time=time(20, 0), end_time=time(8, 0), active=True,
            match_before_minutes=60, match_after_minutes=60,
        ),
    ])
    db.flush()
    roster = {employees[0].id: time(8, 0), employees[1].id: time(20, 0)}
    parsed = {
        "employee_headers": {
            employees[0].id: employees[0].full_name,
            employees[1].id: employees[1].full_name,
        },
        "records": [{"work_date": date(2026, 11, 3), "roster": roster}],
        "issues": [],
        "status": "parsed_for_review",
    }
    prepared = prepare_security_roster(db, parsed)
    assert prepared["days"][0]["can_confirm"] is True
    result = confirm_security_roster_day(
        db, date(2026, 11, 3), roster, admin, [],
    )
    db.commit()
    assert len(result["assignments"]) == 2
    assert db.scalar(select(func.count()).select_from(SecurityShiftAssignment)) == 2


def test_four_person_team_allows_off_and_uses_configured_midnight_pattern():
    from app.security_roster import build_security_day_proposal

    roster = {
        1: time(0, 0),
        2: time(8, 0),
        3: time(16, 0),
        4: "OFF",
    }
    patterns = [
        {"id": 1, "start_time": time(0, 0), "end_time": time(8, 0)},
        {"id": 2, "start_time": time(8, 0), "end_time": time(16, 0)},
        {"id": 3, "start_time": time(16, 0), "end_time": time(0, 0)},
    ]
    proposal = build_security_day_proposal(roster, patterns)

    assert proposal["status"] == "ready_for_admin_review"
    assert len(proposal["assignments"]) == 3
    assert {item["employee_id"] for item in proposal["assignments"]} == {1, 2, 3}
    assert next(item for item in proposal["assignments"] if item["employee_id"] == 1)["start_time"] == time(0, 0)


def test_four_person_team_with_ambiguous_start_is_blocked():
    from app.security_roster import build_security_day_proposal

    roster = {
        1: time(8, 0),
        2: time(12, 0),
        3: time(16, 0),
        4: time(20, 0),
    }
    patterns = [
        {"id": 1, "start_time": time(8, 0), "end_time": time(16, 0)},
        {"id": 2, "start_time": time(8, 0), "end_time": time(20, 0)},
        {"id": 3, "start_time": time(12, 0), "end_time": time(20, 0)},
        {"id": 4, "start_time": time(16, 0), "end_time": time(0, 0)},
        {"id": 5, "start_time": time(20, 0), "end_time": time(4, 0)},
    ]
    proposal = build_security_day_proposal(roster, patterns)

    assert proposal["status"] == "needs_review"
    assert any("shift_pattern_resolution_ambiguous:1" in issue for issue in proposal["issues"])


def test_four_person_team_duplicate_start_times_are_blocked():
    from app.security_roster import build_security_day_proposal

    roster = {
        1: time(8, 0),
        2: time(8, 0),
        3: time(16, 0),
        4: time(20, 0),
    }
    patterns = [
        {"id": 1, "start_time": time(8, 0), "end_time": time(16, 0)},
        {"id": 2, "start_time": time(16, 0), "end_time": time(0, 0)},
        {"id": 3, "start_time": time(20, 0), "end_time": time(4, 0)},
    ]
    proposal = build_security_day_proposal(roster, patterns)

    assert proposal["status"] == "needs_review"
    assert "duplicate_security_shift_start_times" in proposal["issues"]


def test_unmapped_roster_employee_header_blocks_day_confirmation(roster_db):
    db, employees, _admin = roster_db
    parsed = _parsed(employees)
    parsed["issues"] = [{
        "row": 1,
        "column": 5,
        "header": "Unknown Guard",
        "reason": "unknown_or_unmapped_employee_header",
    }]

    prepared = prepare_security_roster(db, parsed)

    assert prepared["days"][0]["can_confirm"] is False
    assert prepared["days"][0]["proposal"]["status"] == "needs_review"
    assert prepared["days"][0]["proposal"]["issues"] == [
        "excel_import_issues_block_this_day"
    ]


def test_adjacent_roster_dates_flag_overnight_overlap_for_hr_review(roster_db):
    db, employees, _admin = roster_db
    department = db.scalar(select(Department).where(Department.name == "Security"))
    for code, start, end in (
        ("LATE-20-08", time(20, 0), time(8, 0)),
        ("DAY-08-20", time(8, 0), time(20, 0)),
        ("NIGHT-00-12", time(0, 0), time(12, 0)),
        ("DAY-12-00", time(12, 0), time(0, 0)),
    ):
        db.add(ShiftPattern(
            department_id=department.id, code=code, name=code,
            start_time=start, end_time=end, active=True,
            match_before_minutes=60, match_after_minutes=60,
        ))
    db.flush()

    first = _parsed(employees, date(2026, 11, 1))
    first["records"][0]["roster"] = {
        employees[0].id: time(20, 0),
        employees[1].id: time(8, 0),
        employees[2].id: "OFF",
    }
    second = _parsed(employees, date(2026, 11, 2))
    second["records"][0]["roster"] = {
        employees[0].id: time(0, 0),
        employees[1].id: time(12, 0),
        employees[2].id: "OFF",
    }
    first["records"].extend(second["records"])

    prepared = prepare_security_roster(db, first)

    assert len(prepared["days"]) == 2
    assert all(day["can_confirm"] is False for day in prepared["days"])
    assert all(day["proposal"]["status"] == "needs_review" for day in prepared["days"])
    assert any(
        issue.startswith("overlapping_security_shifts_across_work_dates:")
        for day in prepared["days"]
        for issue in day["proposal"]["issues"]
    )
    assert db.scalar(select(func.count()).select_from(SecurityShiftAssignment)) == 0


def test_confirmation_rechecks_overnight_overlap_with_adjacent_approved_day(roster_db):
    db, employees, admin = roster_db
    department = db.scalar(select(Department).where(Department.name == "Security"))
    for code, start, end in (
        ("LATE-20-08", time(20, 0), time(8, 0)),
        ("DAY-08-20", time(8, 0), time(20, 0)),
        ("NIGHT-00-12", time(0, 0), time(12, 0)),
        ("DAY-12-00", time(12, 0), time(0, 0)),
    ):
        db.add(ShiftPattern(
            department_id=department.id, code=code, name=code,
            start_time=start, end_time=end, active=True,
            match_before_minutes=60, match_after_minutes=60,
        ))
    db.flush()

    first_roster = {
        employees[0].id: time(20, 0),
        employees[1].id: time(8, 0),
        employees[2].id: "OFF",
    }
    second_roster = {
        employees[0].id: time(0, 0),
        employees[1].id: time(12, 0),
        employees[2].id: "OFF",
    }
    confirm_security_roster_day(
        db, date(2026, 11, 1), first_roster, admin, [],
    )
    db.commit()

    with pytest.raises(
        ValueError,
        match="security_roster_shift_overlaps_adjacent_approved_day",
    ):
        confirm_security_roster_day(
            db, date(2026, 11, 2), second_roster, admin, [],
        )
    db.rollback()
    assert db.scalar(select(func.count()).select_from(SecurityShiftAssignment)) == 3


def test_adjacent_approved_shift_from_now_inactive_security_employee_still_blocks_overlap(roster_db):
    db, employees, admin = roster_db
    department = db.scalar(select(Department).where(Department.name == "Security"))
    for code, start, end in (
        ("LATE-20-08", time(20, 0), time(8, 0)),
        ("DAY-08-20", time(8, 0), time(20, 0)),
        ("NIGHT-00-12", time(0, 0), time(12, 0)),
        ("DAY-12-00", time(12, 0), time(0, 0)),
    ):
        db.add(ShiftPattern(
            department_id=department.id, code=code, name=code,
            start_time=start, end_time=end, active=True,
            match_before_minutes=60, match_after_minutes=60,
        ))
    db.flush()

    first_roster = {
        employees[0].id: time(20, 0),
        employees[1].id: time(8, 0),
        employees[2].id: "OFF",
    }
    confirm_security_roster_day(
        db, date(2026, 11, 1), first_roster, admin, [],
    )
    db.commit()

    employees[0].status = "inactive"
    db.commit()
    second_roster = {
        employees[1].id: time(0, 0),
        employees[2].id: time(12, 0),
    }
    with pytest.raises(
        ValueError,
        match="security_roster_shift_overlaps_adjacent_approved_day",
    ):
        confirm_security_roster_day(
            db, date(2026, 11, 2), second_roster, admin, [],
        )
    db.rollback()
    assert db.scalar(select(func.count()).select_from(SecurityShiftAssignment)) == 2
