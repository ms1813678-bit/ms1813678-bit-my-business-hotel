from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pytest

from app.db import Base
from app.attendance_workflow import (approve_attendance_day, approve_attendance_month, persist_attendance_import, persist_daily_attendance_proposal, prepare_and_persist_monthly_reconciliation, resolve_unknown_attendance_employee, summarize_attendance_month_coverage)
from app.models import (AttendanceImportBatch, AttendanceImportIssue, AttendancePunch, AttendanceDailyRecord, AttendanceMonth, AuditLog, Department, Employee, EmployeeServicePeriod, SecurityShiftAssignment, ShiftPattern)


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
    issue = db_session.scalar(select(AttendanceImportIssue))
    assert issue.reason == "unknown_employee"
    assert issue.source_row == 4


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


def test_daily_attendance_proposal_is_saved_for_review_not_approved(db_session):
    proposal = {
        "work_date": date(2026, 11, 1),
        "shift_pattern_id": 4,
        "scheduled_start_at": datetime(2026, 11, 1, 8, 0),
        "scheduled_end_at": datetime(2026, 11, 1, 16, 0),
        "program_clock_in": datetime(2026, 11, 1, 8, 5),
        "program_clock_out": datetime(2026, 11, 1, 16, 2),
        "first_clock_in": datetime(2026, 11, 1, 8, 5),
        "last_clock_out": datetime(2026, 11, 1, 16, 2),
        "worked_minutes": 477,
        "status": "ready_for_review",
        "issues": [],
    }
    record = persist_daily_attendance_proposal(
        db_session, employee_id=1, proposal=proposal, actor="hr-admin",
        program_import_batch_id=None, fingerprint_import_batch_id=None,
    )
    db_session.commit()

    assert record.status == "ready_for_review"
    assert record.approved_by is None
    assert record.approved_at is None
    assert record.worked_minutes == 477
    assert db_session.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "attendance_day_proposed"
        )
    ) == 1


def test_daily_attendance_proposal_rejects_invalid_status(db_session):
    with pytest.raises(ValueError, match="invalid_attendance_proposal_status"):
        persist_daily_attendance_proposal(
            db_session,
            employee_id=1,
            proposal={"work_date": date(2026, 11, 1), "status": "approved"},
            actor="hr-admin",
        )


def test_monthly_reconciliation_persists_review_records_and_respects_security_off_days(db_session):
    attendance_department = db_session.scalar(
        select(Department).where(Department.name == "Attendance Test")
    )
    security_department = Department(name="Security", active=True)
    db_session.add(security_department)
    db_session.flush()

    security_employees = []
    for index, name in enumerate(("Security A", "Security B", "Security C"), start=1):
        employee = Employee(
            code=f"SEC-ATT-{index}", full_name=name,
            department_id=security_department.id, position="Security",
            hire_date=date(2024, 1, 1), status="active",
            basic_salary=Decimal("5000"), daily_hours=Decimal("8"),
        )
        db_session.add(employee)
        db_session.flush()
        security_employees.append(employee)

    department_pattern = ShiftPattern(
        department_id=attendance_department.id, code="DAY", name="Day",
        start_time=time(8, 0), end_time=time(16, 0),
        match_before_minutes=60, match_after_minutes=60, active=True,
    )
    security_night = ShiftPattern(
        department_id=security_department.id, code="NIGHT", name="Night",
        start_time=time(20, 0), end_time=time(8, 0),
        match_before_minutes=60, match_after_minutes=60, active=True,
    )
    security_day = ShiftPattern(
        department_id=security_department.id, code="DAY", name="Day",
        start_time=time(8, 0), end_time=time(20, 0),
        match_before_minutes=60, match_after_minutes=60, active=True,
    )
    db_session.add_all([department_pattern, security_night, security_day])
    db_session.flush()

    work_date = date(2026, 11, 1)
    program_batch = AttendanceImportBatch(
        source_type="program_report", period_start=work_date, period_end=work_date,
        original_filename="program.xlsx", file_sha256="a" * 64,
        uploaded_by="hr-admin", status="parsed", row_count=2, issue_count=0,
    )
    fingerprint_batch = AttendanceImportBatch(
        source_type="fingerprint", period_start=work_date, period_end=work_date,
        original_filename="fingerprint.xlsx", file_sha256="b" * 64,
        uploaded_by="hr-admin", status="parsed", row_count=6, issue_count=0,
    )
    db_session.add_all([program_batch, fingerprint_batch])
    db_session.flush()

    db_session.add_all([
        SecurityShiftAssignment(
            employee_id=security_employees[0].id, work_date=work_date,
            shift_pattern_id=security_night.id,
        ),
        SecurityShiftAssignment(
            employee_id=security_employees[1].id, work_date=work_date,
            shift_pattern_id=security_day.id,
        ),
        AuditLog(
            actor="hr-admin", action="security_roster_day_approved",
            entity_type="security_roster", entity_id=work_date.isoformat(),
            reason="Approved security roster for test",
        ),
    ])
    db_session.flush()

    normal_employee = db_session.scalar(
        select(Employee).where(Employee.code == "ATT-001")
    )
    db_session.add_all([
        AttendancePunch(
            import_batch_id=program_batch.id, employee_id=normal_employee.id,
            raw_employee_code="ATT-001", raw_employee_name="Test Employee",
            punch_at=datetime(2026, 11, 1, 8, 0), raw_timestamp="08:00",
            event_type="IN", source_sheet="Sheet1", source_row=2, event_index=0,
        ),
        AttendancePunch(
            import_batch_id=program_batch.id, employee_id=normal_employee.id,
            raw_employee_code="ATT-001", raw_employee_name="Test Employee",
            punch_at=datetime(2026, 11, 1, 16, 0), raw_timestamp="16:00",
            event_type="OUT", source_sheet="Sheet1", source_row=2, event_index=1,
        ),
    ])
    for employee_index, (employee, start, end) in enumerate((
        (normal_employee, datetime(2026, 11, 1, 8, 0), datetime(2026, 11, 1, 16, 0)),
        (security_employees[0], datetime(2026, 11, 1, 20, 0), datetime(2026, 11, 2, 8, 0)),
        (security_employees[1], datetime(2026, 11, 1, 8, 0), datetime(2026, 11, 1, 20, 0)),
    )):
        for index, punch_at in enumerate((start, end)):
            db_session.add(AttendancePunch(
                import_batch_id=fingerprint_batch.id, employee_id=employee.id,
                raw_employee_code=employee.code, raw_employee_name=employee.full_name,
                punch_at=punch_at, raw_timestamp=punch_at.strftime("%H:%M"),
                event_type="PUNCH", source_sheet="Att.log report",
                source_row=2 + employee_index * 2 + index, event_index=0,
            ))

    summary = prepare_and_persist_monthly_reconciliation(
        db_session,
        program_batch_id=program_batch.id,
        fingerprint_batch_id=fingerprint_batch.id,
        period_start=work_date,
        period_end=work_date,
        actor="hr-admin",
    )
    db_session.commit()

    assert summary == {
        "total": 3, "ready_for_review": 3, "needs_review": 0,
        "import_issue_count": 0,
        "period_start": work_date, "period_end": work_date,
    }
    records = db_session.scalars(select(AttendanceDailyRecord)).all()
    assert len(records) == 3
    assert all(item.status == "ready_for_review" for item in records)
    assert all(item.approved_by is None and item.approved_at is None for item in records)
    assert security_employees[2].id not in {item.employee_id for item in records}


def test_monthly_reconciliation_refuses_duplicate_period_records(db_session):
    work_date = date(2026, 11, 1)
    program_batch = AttendanceImportBatch(
        source_type="program_report", period_start=work_date, period_end=work_date,
        original_filename="program.xlsx", file_sha256="c" * 64,
        uploaded_by="hr-admin", status="parsed",
    )
    fingerprint_batch = AttendanceImportBatch(
        source_type="fingerprint", period_start=work_date, period_end=work_date,
        original_filename="fingerprint.xlsx", file_sha256="d" * 64,
        uploaded_by="hr-admin", status="parsed",
    )
    db_session.add_all([program_batch, fingerprint_batch])
    db_session.flush()
    db_session.add(AttendanceDailyRecord(
        employee_id=1, work_date=work_date, status="needs_review",
    ))
    db_session.flush()

    with pytest.raises(ValueError, match="attendance_period_already_has_reconciliation_records"):
        prepare_and_persist_monthly_reconciliation(
            db_session, program_batch_id=program_batch.id,
            fingerprint_batch_id=fingerprint_batch.id,
            period_start=work_date, period_end=work_date, actor="hr-admin",
        )


def test_monthly_reconciliation_is_blocked_until_both_source_imports_are_clean(db_session):
    work_date = date(2026, 11, 1)
    program = AttendanceImportBatch(
        source_type="program_report", period_start=work_date, period_end=work_date,
        original_filename="program.xlsx", file_sha256="e" * 64,
        uploaded_by="hr-admin", status="needs_review", row_count=2, issue_count=1,
    )
    fingerprint = AttendanceImportBatch(
        source_type="fingerprint", period_start=work_date, period_end=work_date,
        original_filename="fingerprint.xlsx", file_sha256="f" * 64,
        uploaded_by="hr-admin", status="parsed", row_count=2, issue_count=0,
    )
    db_session.add_all([program, fingerprint])
    db_session.flush()

    with pytest.raises(ValueError, match="attendance_import_batch_has_unresolved_issues"):
        prepare_and_persist_monthly_reconciliation(
            db_session, program_batch_id=program.id, fingerprint_batch_id=fingerprint.id,
            period_start=work_date, period_end=work_date, actor="hr-admin",
        )
    assert db_session.scalar(select(func.count()).select_from(AttendanceDailyRecord)) == 0


def test_partial_fingerprint_date_range_joins_the_program_month(db_session):
    program_payload = parsed_payload()
    program_payload["source_type"] = "program_report"
    program_payload["period_start"] = date(2026, 11, 1)
    program_payload["period_end"] = date(2026, 11, 30)
    program = persist_attendance_import(
        db_session, file_bytes=b"program-whole-month",
        filename="program.xlsx", parsed=program_payload, actor="hr-admin",
    )

    fingerprint_payload = parsed_payload()
    fingerprint_payload["period_start"] = date(2026, 11, 3)
    fingerprint_payload["period_end"] = date(2026, 11, 28)
    fingerprint = persist_attendance_import(
        db_session, file_bytes=b"fingerprint-punch-days-only",
        filename="fingerprint.xlsx", parsed=fingerprint_payload, actor="hr-admin",
    )

    assert fingerprint.period_start == date(2026, 11, 1)
    assert fingerprint.period_end == date(2026, 11, 30)
    month = db_session.scalar(select(AttendanceMonth))
    assert month.period_start == program.period_start
    assert month.period_end == program.period_end
    assert month.program_import_batch_id == program.id
    assert month.fingerprint_import_batch_id == fingerprint.id


def test_unresolved_source_batch_can_be_replaced_before_reconciliation(db_session):
    payload = parsed_payload()
    payload["issues"] = [{"sheet": "Sheet1", "row": 2, "reason": "invalid_time_value", "value": "bad"}]
    old_batch = persist_attendance_import(
        db_session, file_bytes=b"bad-source-version", filename="program.xlsx",
        parsed={**payload, "source_type": "program_report"}, actor="hr-admin",
    )
    new_payload = {**parsed_payload(), "source_type": "program_report"}
    new_batch = persist_attendance_import(
        db_session, file_bytes=b"corrected-source-version", filename="program-corrected.xlsx",
        parsed=new_payload, actor="hr-admin",
    )

    month = db_session.scalar(select(AttendanceMonth))
    assert old_batch.status == "rejected"
    assert new_batch.status == "parsed"
    assert month.program_import_batch_id == new_batch.id
    assert db_session.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == "attendance_import_batch_replaced"
    )) == 1


def test_hr_approval_preserves_source_evidence_and_writes_audit(db_session):
    record = persist_daily_attendance_proposal(
        db_session,
        employee_id=1,
        proposal={
            "work_date": date(2026, 11, 1),
            "program_clock_in": datetime(2026, 11, 1, 8, 3),
            "program_clock_out": datetime(2026, 11, 1, 16, 1),
            "first_clock_in": datetime(2026, 11, 1, 8, 5),
            "last_clock_out": datetime(2026, 11, 1, 16, 0),
            "worked_minutes": 475,
            "status": "needs_review",
            "issues": ["source_disagreement"],
        },
        actor="hr-admin",
    )
    approved = approve_attendance_day(
        db_session,
        record_id=record.id,
        clock_in=datetime(2026, 11, 1, 8, 4),
        clock_out=datetime(2026, 11, 1, 16, 2),
        actor="hr-admin",
        reason="Reviewed source discrepancy against original documents",
    )
    db_session.commit()

    assert approved.status == "approved"
    assert approved.approved_by == "hr-admin"
    assert approved.approved_at is not None
    assert approved.approved_clock_in == datetime(2026, 11, 1, 8, 4)
    assert approved.approved_clock_out == datetime(2026, 11, 1, 16, 2)
    assert approved.worked_minutes == 478
    assert approved.program_clock_in == datetime(2026, 11, 1, 8, 3)
    assert approved.program_clock_out == datetime(2026, 11, 1, 16, 1)
    assert approved.first_clock_in == datetime(2026, 11, 1, 8, 5)
    assert approved.last_clock_out == datetime(2026, 11, 1, 16, 0)
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "attendance_day_approved")
    )
    assert audit is not None
    assert audit.actor == "hr-admin"
    assert "Reviewed source discrepancy" in audit.reason


def test_hr_approval_cannot_be_applied_twice(db_session):
    record = persist_daily_attendance_proposal(
        db_session, employee_id=1,
        proposal={
            "work_date": date(2026, 11, 1),
            "status": "ready_for_review",
            "issues": [],
        },
        actor="hr-admin",
    )
    approve_attendance_day(
        db_session, record_id=record.id,
        clock_in=datetime(2026, 11, 1, 8, 0),
        clock_out=datetime(2026, 11, 1, 16, 0),
        actor="hr-admin", reason="Verified",
    )
    with pytest.raises(ValueError, match="attendance_daily_record_already_approved"):
        approve_attendance_day(
            db_session, record_id=record.id,
            clock_in=datetime(2026, 11, 1, 8, 0),
            clock_out=datetime(2026, 11, 1, 16, 0),
            actor="hr-admin", reason="Second approval attempt",
        )


def test_hr_approval_requires_reason_and_positive_duration(db_session):
    record = persist_daily_attendance_proposal(
        db_session, employee_id=1,
        proposal={
            "work_date": date(2026, 11, 1),
            "status": "ready_for_review",
            "issues": [],
        },
        actor="hr-admin",
    )
    with pytest.raises(ValueError, match="attendance_approval_reason_required"):
        approve_attendance_day(
            db_session, record_id=record.id,
            clock_in=datetime(2026, 11, 1, 8, 0),
            clock_out=datetime(2026, 11, 1, 16, 0),
            actor="hr-admin", reason=" ",
        )
    with pytest.raises(ValueError, match="attendance_clock_out_must_follow_clock_in"):
        approve_attendance_day(
            db_session, record_id=record.id,
            clock_in=datetime(2026, 11, 1, 16, 0),
            clock_out=datetime(2026, 11, 1, 8, 0),
            actor="hr-admin", reason="Invalid interval",
        )


def test_monthly_imports_create_one_imported_month_and_link_both_sources(db_session):
    program_payload = parsed_payload()
    program_payload["source_type"] = "program_report"
    fingerprint_payload = parsed_payload()
    fingerprint_payload["source_type"] = "fingerprint"
    program = persist_attendance_import(
        db_session, file_bytes=b"program-month-file", filename="program.xlsx",
        parsed=program_payload, actor="hr-admin",
    )
    fingerprint = persist_attendance_import(
        db_session, file_bytes=b"fingerprint-month-file", filename="fingerprint.xlsx",
        parsed=fingerprint_payload, actor="hr-admin",
    )
    month = db_session.scalar(select(AttendanceMonth))
    assert month is not None
    assert month.status == "imported"
    assert month.program_import_batch_id == program.id
    assert month.fingerprint_import_batch_id == fingerprint.id
    assert month.created_by == "hr-admin"


def test_month_approval_requires_daily_approval_and_explicit_coverage(db_session):
    work_date = date(2026, 11, 1)
    program = AttendanceImportBatch(
        source_type="program_report", period_start=work_date, period_end=work_date,
        original_filename="program.xlsx", file_sha256="1" * 64,
        uploaded_by="hr-admin", status="parsed",
    )
    fingerprint = AttendanceImportBatch(
        source_type="fingerprint", period_start=work_date, period_end=work_date,
        original_filename="fingerprint.xlsx", file_sha256="2" * 64,
        uploaded_by="hr-admin", status="parsed",
    )
    db_session.add_all([program, fingerprint])
    db_session.flush()
    future_rehire = Employee(
        code="ATT-FUTURE-REHIRE", full_name="Future Rehire",
        department_id=db_session.scalar(
            select(Department.id).where(Department.name == "Attendance Test")
        ),
        position="Test", hire_date=date(2020, 1, 1), status="active",
        basic_salary=Decimal("5000"), daily_hours=Decimal("8"),
    )
    db_session.add(future_rehire)
    db_session.flush()
    db_session.add(EmployeeServicePeriod(
        employee_id=future_rehire.id,
        start_date=date(2026, 12, 1),
        end_date=None,
        start_reason="Future rehire",
    ))
    month = AttendanceMonth(
        period_start=work_date, period_end=work_date,
        program_import_batch_id=program.id,
        fingerprint_import_batch_id=fingerprint.id,
        status="under_review", created_by="hr-admin",
    )
    db_session.add(month)
    db_session.flush()
    pending = AttendanceDailyRecord(
        employee_id=1, work_date=work_date, status="needs_review",
        program_import_batch_id=program.id,
        fingerprint_import_batch_id=fingerprint.id,
    )
    # An unrelated historical row in the same calendar range must not affect this
    # month's approval decision or counts.
    other_employee = Employee(
        code="ATT-OTHER", full_name="Other Historical Employee",
        department_id=db_session.scalar(select(Employee.department_id).where(Employee.id == 1)),
        position="Test", hire_date=date(2024, 1, 1), status="active",
        basic_salary=Decimal("4000"), daily_hours=Decimal("8"),
    )
    db_session.add(other_employee)
    db_session.flush()
    db_session.add(AttendanceDailyRecord(
        employee_id=other_employee.id, work_date=work_date, status="needs_review",
    ))
    db_session.add(pending)
    db_session.flush()

    with pytest.raises(ValueError, match="attendance_month_has_unapproved_daily_records"):
        approve_attendance_month(
            db_session, month_id=month.id, actor="hr-admin",
            reason="Reviewed the month", coverage_confirmed=True,
        )
    program.status = "needs_review"
    with pytest.raises(ValueError, match="attendance_month_source_batch_not_ready_for_approval"):
        approve_attendance_month(
            db_session, month_id=month.id, actor="hr-admin",
            reason="Reviewed the month", coverage_confirmed=True,
        )
    program.status = "parsed"
    pending.status = "approved"
    pending.approved_by = "hr-admin"
    pending.approved_at = datetime(2026, 11, 2)
    with pytest.raises(ValueError, match="attendance_month_coverage_confirmation_required"):
        approve_attendance_month(
            db_session, month_id=month.id, actor="hr-admin",
            reason="Reviewed the month", coverage_confirmed=False,
        )
    approved = approve_attendance_month(
        db_session, month_id=month.id, actor="hr-admin",
        reason="All employee/day gaps checked against source reports",
        coverage_confirmed=True,
    )
    assert approved.status == "approved"
    assert approved.approved_by == "hr-admin"
    assert approved.approved_at is not None
    assert program.status == "approved"
    assert fingerprint.status == "approved"
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "attendance_month_approved")
    )
    assert audit is not None
    assert audit.entity_id == str(month.id)



def test_monthly_coverage_summary_surfaces_blank_employee_days_without_marking_absence(db_session):
    work_date = date(2026, 11, 1)
    security = Department(name="Security", active=True)
    db_session.add(security)
    db_session.flush()
    security_employees = []
    for index in range(3):
        employee = Employee(
            code=f"SEC-COVER-{index}", full_name=f"Security Cover {index}",
            department_id=security.id, position="Security",
            hire_date=date(2024, 1, 1), status="active",
            basic_salary=Decimal("5000"), daily_hours=Decimal("8"),
        )
        security_employees.append(employee)
        db_session.add(employee)
    program = AttendanceImportBatch(
        source_type="program_report", period_start=work_date, period_end=work_date,
        original_filename="program.xlsx", file_sha256="1" * 64,
        uploaded_by="hr-admin", status="parsed", row_count=1, issue_count=0,
    )
    fingerprint = AttendanceImportBatch(
        source_type="fingerprint", period_start=work_date, period_end=work_date,
        original_filename="fingerprint.xlsx", file_sha256="2" * 64,
        uploaded_by="hr-admin", status="parsed", row_count=1, issue_count=0,
    )
    db_session.add_all([program, fingerprint])
    db_session.flush()
    month = AttendanceMonth(
        period_start=work_date, period_end=work_date,
        program_import_batch_id=program.id,
        fingerprint_import_batch_id=fingerprint.id,
        status="under_review", created_by="hr-admin",
    )
    db_session.add(month)
    db_session.add(AuditLog(
        actor="hr-admin", action="security_roster_day_approved",
        entity_type="security_roster", entity_id=work_date.isoformat(),
        reason="Approved roster; blank assignment means OFF",
    ))
    pattern = ShiftPattern(
        department_id=security.id, code="SEC-08-20", name="Security 08-20",
        start_time=time(8, 0), end_time=time(20, 0),
        match_before_minutes=60, match_after_minutes=60, active=True,
    )
    db_session.add(pattern)
    db_session.flush()
    db_session.add(SecurityShiftAssignment(
        employee_id=security_employees[0].id, work_date=work_date,
        shift_pattern_id=pattern.id, notes="Approved security roster assignment",
    ))
    db_session.flush()

    summary = summarize_attendance_month_coverage(db_session, month)
    assert summary["unrepresented_employee_days"] == 2
    assert {
        (gap["employee_code"], gap["work_date"])
        for gap in summary["coverage_examples"]
    } == {("ATT-001", work_date), ("SEC-COVER-0", work_date)}


def test_unknown_employee_resolution_links_raw_punches_and_recalculates_batch_status(db_session):
    payload = parsed_payload()
    payload["events"] = [{
        "employee_id": None,
        "raw_employee_code": "FP-UNKNOWN",
        "raw_employee_name": "Unmatched Name",
        "punch_at": datetime(2026, 11, 1, 7, 50),
        "raw_timestamp": "07:50",
        "event_type": "PUNCH",
        "source_sheet": "Att.log report",
        "source_row": 9,
        "event_index": 0,
    }, {
        "employee_id": None,
        "raw_employee_code": "FP-UNKNOWN",
        "raw_employee_name": "Unmatched Name",
        "punch_at": datetime(2026, 11, 1, 16, 9),
        "raw_timestamp": "07:50 16:09",
        "event_type": "PUNCH",
        "source_sheet": "Att.log report",
        "source_row": 9,
        "event_index": 1,
    }]
    payload["issues"] = [{
        "sheet": "Att.log report", "row": 9,
        "reason": "unknown_employee", "value": "FP-UNKNOWN",
    }]
    batch = persist_attendance_import(
        db_session, file_bytes=b"unknown-identity-file",
        filename="fingerprint.xlsx", parsed=payload, actor="hr-admin",
    )
    issue = db_session.scalar(select(AttendanceImportIssue).where(
        AttendanceImportIssue.import_batch_id == batch.id
    ))
    employee = db_session.scalar(select(Employee).where(Employee.code == "ATT-001"))

    resolved = resolve_unknown_attendance_employee(
        db_session, issue_id=issue.id, employee_id=employee.id, actor="hr-admin"
    )
    db_session.flush()

    punches = db_session.scalars(select(AttendancePunch).where(
        AttendancePunch.import_batch_id == batch.id
    ).order_by(AttendancePunch.event_index)).all()
    assert len(punches) == 2
    assert all(punch.employee_id == employee.id for punch in punches)
    assert all(punch.raw_employee_code == "FP-UNKNOWN" for punch in punches)
    assert punches[0].raw_timestamp == "07:50"
    assert punches[1].raw_timestamp == "07:50 16:09"
    assert resolved.resolved_employee_id == employee.id
    assert resolved.resolved_by == "hr-admin"
    assert resolved.resolved_at is not None
    assert batch.issue_count == 0
    assert batch.status == "parsed"
    audit = db_session.scalar(select(AuditLog).where(
        AuditLog.action == "attendance_import_unknown_employee_resolved"
    ))
    assert audit is not None
    assert audit.actor == "hr-admin"


def test_unknown_employee_resolution_rejects_reassignment_and_wrong_issue_type(db_session):
    payload = parsed_payload()
    payload["events"] = [{
        "employee_id": None, "raw_employee_code": "FP-UNKNOWN",
        "raw_employee_name": "Unknown", "punch_at": datetime(2026, 11, 1, 7, 50),
        "raw_timestamp": "07:50", "event_type": "PUNCH",
        "source_sheet": "Att.log report", "source_row": 4, "event_index": 0,
    }]
    payload["issues"] = [{
        "sheet": "Att.log report", "row": 4,
        "reason": "unknown_employee", "value": "FP-UNKNOWN",
    }]
    batch = persist_attendance_import(
        db_session, file_bytes=b"unknown-identity-file-2",
        filename="fingerprint.xlsx", parsed=payload, actor="hr-admin",
    )
    issue = db_session.scalar(select(AttendanceImportIssue).where(
        AttendanceImportIssue.import_batch_id == batch.id
    ))
    employee = db_session.scalar(select(Employee).where(Employee.code == "ATT-001"))
    resolve_unknown_attendance_employee(
        db_session, issue_id=issue.id, employee_id=employee.id, actor="hr-admin"
    )
    with pytest.raises(ValueError, match="attendance_import_issue_already_resolved"):
        resolve_unknown_attendance_employee(
            db_session, issue_id=issue.id, employee_id=employee.id, actor="hr-admin"
        )
    invalid_issue = AttendanceImportIssue(
        import_batch_id=batch.id, source_sheet="Att.log report",
        source_row=10, reason="invalid_time_value",
    )
    db_session.add(invalid_issue)
    db_session.flush()
    with pytest.raises(ValueError, match="attendance_import_issue_not_identity_resolvable"):
        resolve_unknown_attendance_employee(
            db_session, issue_id=invalid_issue.id, employee_id=employee.id, actor="hr-admin"
        )
