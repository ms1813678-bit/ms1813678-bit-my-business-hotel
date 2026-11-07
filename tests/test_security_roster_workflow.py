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
    approve_security_roster_month,
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
    result = confirm_security_roster_day(
        db, day["work_date"], day["roster"], admin, parsed["issues"]
    )
    db.commit()
    assert len(result["assignments"]) == 3
    assert {item["duration_hours"] for item in result["assignments"]} == {8}
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




def test_two_person_day_uses_twelve_hour_patterns_and_third_is_off(roster_db):
    db, employees, admin = roster_db
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
    roster = {
        employees[0].id: time(8, 0),
        employees[1].id: time(20, 0),
        employees[2].id: "OFF",
    }
    parsed = {
        "employee_headers": {employee.id: employee.full_name for employee in employees},
        "records": [{"work_date": date(2026, 11, 3), "roster": roster}],
        "issues": [], "status": "parsed_for_review",
    }
    prepared = prepare_security_roster(db, parsed)
    assert prepared["days"][0]["can_confirm"] is True
    result = confirm_security_roster_day(db, date(2026, 11, 3), roster, admin, [])
    db.commit()
    assert len(result["assignments"]) == 2
    assert {item["duration_hours"] for item in result["assignments"]} == {12}


def test_four_working_guards_use_eight_hour_patterns_but_overlap_still_blocks_approval():
    from app.security_roster import build_security_day_proposal

    starts = [time(0, 0), time(4, 0), time(8, 0), time(12, 0)]
    patterns = [
        {"id": index, "start_time": start, "end_time": time((start.hour + 8) % 24, 0)}
        for index, start in enumerate(starts, start=1)
    ]
    proposal = build_security_day_proposal(
        {index: start for index, start in enumerate(starts, start=1)},
        patterns,
    )
    assert len(proposal["assignments"]) == 4
    assert {item["duration_hours"] for item in proposal["assignments"]} == {8}
    # Current no-overlap guard remains fail-closed pending HR handover policy.
    assert proposal["status"] == "needs_review"
    assert any(issue.startswith("overlapping_security_shifts:") for issue in proposal["issues"])


def test_one_working_guard_is_rejected_even_with_three_employee_roster():
    from app.security_roster import build_security_day_proposal
    proposal = build_security_day_proposal(
        {1: time(8, 0), 2: "OFF", 3: "OFF"},
        [{"id": 1, "start_time": time(8, 0), "end_time": time(20, 0)}],
    )
    assert proposal["status"] == "needs_review"
    assert proposal["assignments"] == []
    assert "daily_security_working_count_must_be_at_least_two" in proposal["issues"]


def test_one_active_security_employee_is_rejected_but_four_are_supported(roster_db):
    db, employees, _admin = roster_db
    department = db.scalar(select(Department).where(Department.name == "Security"))
    employees[1].status = "inactive"
    employees[2].status = "inactive"
    db.flush()
    with pytest.raises(ValueError, match="security_department_must_have_at_least_two_active_employees"):
        prepare_security_roster(db, _parsed(employees[:1]))
    employees[1].status = "active"
    employees[2].status = "active"
    db.flush()
    fourth = Employee(
        code="SEC-04", full_name="Synthetic fourth", department_id=department.id,
        position="Security", hire_date=date(2024, 1, 1),
        basic_salary=Decimal("5000"), daily_hours=Decimal("8"), status="active",
    )
    db.add(fourth)
    db.flush()
    four_employee_roster = _parsed(employees + [fourth])
    four_employee_roster["records"][0]["roster"][fourth.id] = "OFF"
    prepared = prepare_security_roster(db, four_employee_roster)
    assert len(prepared["employee_headers"]) == 4
    assert prepared["days"][0]["can_confirm"] is True


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


def test_roster_preview_marks_already_approved_date_and_disables_confirmation(roster_db):
    db, employees, admin = roster_db
    parsed = _parsed(employees, date(2026, 11, 7))
    day = prepare_security_roster(db, parsed)["days"][0]
    confirm_security_roster_day(
        db, day["work_date"], day["roster"], admin, parsed["issues"]
    )
    db.commit()

    refreshed = prepare_security_roster(db, parsed)
    approved_day = refreshed["days"][0]

    assert approved_day["already_approved"] is True
    assert approved_day["can_confirm"] is False


def test_security_month_approval_requires_every_day_to_be_reviewed(roster_db):
    db, employees, admin = roster_db
    parsed = _parsed(employees, date(2026, 11, 1))
    day = prepare_security_roster(db, parsed)["days"][0]
    confirm_security_roster_day(
        db, day["work_date"], day["roster"], admin, parsed["issues"]
    )
    db.commit()

    with pytest.raises(
        ValueError,
        match="security_roster_month_has_unreviewed_days",
    ):
        approve_security_roster_month(
            db, date(2026, 11, 1), admin, "Reviewed the Security month",
        )


def test_security_month_approval_requires_reason_and_writes_audit(roster_db):
    db, employees, admin = roster_db
    for day_number in range(1, 31):
        work_date = date(2026, 11, day_number)
        parsed = _parsed(employees, work_date)
        day = prepare_security_roster(db, parsed)["days"][0]
        confirm_security_roster_day(
            db, work_date, day["roster"], admin, parsed["issues"]
        )

    with pytest.raises(
        ValueError,
        match="security_roster_month_approval_reason_required",
    ):
        approve_security_roster_month(db, date(2026, 11, 1), admin, "   ")

    approval = approve_security_roster_month(
        db,
        date(2026, 11, 1),
        admin,
        "راجعت واعتمدت جميع أيام كشف الأمن لشهر نوفمبر 2026",
    )
    db.commit()

    assert approval.action == "security_roster_month_approved"
    assert approval.entity_id == "2026-11"
    assert "reviewed_days=30" in approval.new_value
    assert approval.reason == "راجعت واعتمدت جميع أيام كشف الأمن لشهر نوفمبر 2026"

    with pytest.raises(ValueError, match="security_roster_month_already_approved"):
        approve_security_roster_month(
            db, date(2026, 11, 1), admin, "Duplicate approval attempt",
        )


def test_security_month_approval_rejects_assignment_added_after_daily_review(roster_db):
    db, employees, admin = roster_db
    for day_number in range(1, 31):
        work_date = date(2026, 11, day_number)
        parsed = _parsed(employees, work_date)
        confirm_security_roster_day(
            db, work_date, parsed["records"][0]["roster"], admin, [],
        )

    department = db.scalar(select(Department).where(Department.name == "Security"))
    pattern = db.scalar(select(ShiftPattern).where(
        ShiftPattern.department_id == department.id,
        ShiftPattern.start_time == time(0, 0),
        ShiftPattern.end_time == time(8, 0),
    ))
    extra = Employee(
        code="SEC-EXTRA-MONTH",
        full_name="Additional Security Employee",
        department_id=department.id,
        position="Security",
        hire_date=date(2024, 1, 1),
        status="active",
        basic_salary=Decimal("5000"),
        daily_hours=Decimal("8"),
    )
    db.add(extra)
    db.flush()
    db.add(SecurityShiftAssignment(
        employee_id=extra.id,
        work_date=date(2026, 11, 1),
        shift_pattern_id=pattern.id,
        notes="Unexpected assignment after daily review",
    ))
    db.flush()

    with pytest.raises(
        ValueError,
        match="security_roster_month_daily_evidence_mismatch",
    ):
        approve_security_roster_month(
            db, date(2026, 11, 1), admin, "Review must reject late assignment",
        )


def test_security_month_approval_rejects_non_security_pattern_for_rostered_employee(roster_db):
    from sqlalchemy import update

    db, employees, admin = roster_db
    for day_number in range(1, 31):
        work_date = date(2026, 11, day_number)
        parsed = _parsed(employees, work_date)
        confirm_security_roster_day(
            db, work_date, parsed["records"][0]["roster"], admin, [],
        )

    reception = Department(name="Reception", active=True)
    db.add(reception)
    db.flush()
    foreign_pattern = ShiftPattern(
        department_id=reception.id,
        code="REC-TEST",
        name="Foreign department pattern",
        start_time=time(0, 0),
        end_time=time(8, 0),
        active=True,
    )
    db.add(foreign_pattern)
    db.flush()
    assignment = db.scalar(select(SecurityShiftAssignment).where(
        SecurityShiftAssignment.employee_id == employees[0].id,
        SecurityShiftAssignment.work_date == date(2026, 11, 1),
    ))
    db.execute(
        update(SecurityShiftAssignment)
        .where(SecurityShiftAssignment.id == assignment.id)
        .values(shift_pattern_id=foreign_pattern.id)
    )
    db.flush()

    with pytest.raises(
        ValueError,
        match="security_roster_month_contains_assignment_with_non_security_pattern",
    ):
        approve_security_roster_month(
            db, date(2026, 11, 1), admin, "Review wrong department assignment",
        )


def test_security_month_approval_rejects_duplicate_employee_ids_in_daily_audit(roster_db):
    from sqlalchemy import update

    db, employees, admin = roster_db
    for day_number in range(1, 31):
        work_date = date(2026, 11, day_number)
        parsed = _parsed(employees, work_date)
        confirm_security_roster_day(
            db, work_date, parsed["records"][0]["roster"], admin, [],
        )

    original = db.scalar(select(AuditLog).where(
        AuditLog.action == "security_roster_day_approved",
        AuditLog.entity_id == "2026-11-01",
    ))
    db.execute(
        update(AuditLog)
        .where(AuditLog.id == original.id)
        .values(new_value=(
            f"{employees[0].id}:00:00; {employees[0].id}:OFF; "
            f"{employees[1].id}:08:00; {employees[2].id}:16:00"
        ))
    )
    db.flush()

    with pytest.raises(
        ValueError,
        match="security_roster_month_daily_audit_has_duplicate_employees",
    ):
        approve_security_roster_month(
            db, date(2026, 11, 1), admin, "Reject duplicate audit employee",
        )
