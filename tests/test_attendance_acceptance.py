"""End-to-end acceptance of the HR attendance evidence and approval path."""
from datetime import date, datetime, time
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.attendance_import import parse_attendance_workbook
from app.attendance_workflow import (
    approve_attendance_day,
    approve_attendance_month,
    persist_attendance_import,
    prepare_and_persist_monthly_reconciliation,
)
from app.models import (
    AttendanceDailyRecord,
    AttendanceImportBatch,
    AttendanceMonth,
    AttendancePunch,
    AuditLog,
    Department,
    Employee,
    ShiftPattern,
)


def _xlsx(rows):
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_program_and_fingerprint_import_reconcile_then_require_hr_approval():
    """Both sources feed one month; disagreement stays pending until HR decides."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    work_date = date(2026, 11, 1)
    with factory.begin() as db:
        reception = Department(name="Reception", active=True)
        security = Department(name="Security", active=True)
        db.add_all([reception, security])
        db.flush()

        employee = Employee(
            code="REC-ACCEPT-01",
            full_name="Synthetic Reception Employee",
            fingerprint_id="FP-REC-01",
            department_id=reception.id,
            position="Reception",
            hire_date=date(2024, 1, 1),
            status="active",
            basic_salary=Decimal("5000"),
            daily_hours=Decimal("8"),
        )
        db.add(employee)
        db.flush()
        db.add(ShiftPattern(
            department_id=reception.id,
            code="REC-DAY",
            name="Reception day",
            start_time=time(8, 0),
            end_time=time(16, 0),
            match_before_minutes=60,
            match_after_minutes=60,
            active=True,
        ))
        employee_id = employee.id

    aliases = {
        "REC-ACCEPT-01": employee_id,
        "FP-REC-01": employee_id,
        "Synthetic Reception Employee": employee_id,
    }
    program_bytes = _xlsx([
        ["AC-No.", "Name", "Date", "Clock In", "Clock Out"],
        ["REC-ACCEPT-01", "Synthetic Reception Employee", work_date, time(8, 5), time(16, 3)],
    ])
    fingerprint_bytes = _xlsx([
        ["AC-No.", "Name", "Date", "Check Time"],
        ["FP-REC-01", "Synthetic Reception Employee", work_date, "08:04 16:02"],
    ])

    program_parsed = parse_attendance_workbook(
        program_bytes, "program-november.xlsx", "program_report", aliases
    )
    fingerprint_parsed = parse_attendance_workbook(
        fingerprint_bytes, "fingerprint-november.xlsx", "fingerprint", aliases
    )
    assert program_parsed["issues"] == []
    assert fingerprint_parsed["issues"] == []

    with factory.begin() as db:
        program = persist_attendance_import(
            db,
            file_bytes=program_bytes,
            filename="program-november.xlsx",
            parsed=program_parsed,
            actor="hr-admin",
        )
        fingerprint = persist_attendance_import(
            db,
            file_bytes=fingerprint_bytes,
            filename="fingerprint-november.xlsx",
            parsed=fingerprint_parsed,
            actor="hr-admin",
        )
        program_id, fingerprint_id = program.id, fingerprint.id

    with factory.begin() as db:
        summary = prepare_and_persist_monthly_reconciliation(
            db,
            program_batch_id=program_id,
            fingerprint_batch_id=fingerprint_id,
            period_start=work_date,
            period_end=work_date,
            actor="hr-admin",
        )
        assert summary["total"] == 1
        assert summary["needs_review"] == 1
        record = db.scalar(select(AttendanceDailyRecord))
        assert record.status == "needs_review"
        assert record.program_clock_in == datetime(2026, 11, 1, 8, 5)
        assert record.first_clock_in == datetime(2026, 11, 1, 8, 4)
        record_id = record.id

    # The source mismatch is not silently resolved. HR explicitly chooses and
    # records the final clock values with a reason before month approval.
    with factory.begin() as db:
        approved_day = approve_attendance_day(
            db,
            record_id=record_id,
            clock_in=datetime(2026, 11, 1, 8, 5),
            clock_out=datetime(2026, 11, 1, 16, 3),
            actor="hr-admin",
            reason="Compared the two source reports and approved the primary program values",
        )
        assert approved_day.status == "approved"
        assert approved_day.approved_clock_in == datetime(2026, 11, 1, 8, 5)
        assert approved_day.approved_clock_out == datetime(2026, 11, 1, 16, 3)

    with factory.begin() as db:
        month = db.scalar(select(AttendanceMonth))
        assert month.status == "under_review"
        approved_month = approve_attendance_month(
            db,
            month_id=month.id,
            actor="hr-admin",
            reason="Checked employee/day coverage against both source reports",
            coverage_confirmed=True,
        )
        assert approved_month.status == "approved"
        assert approved_month.approved_by == "hr-admin"

    with factory() as db:
        assert db.scalar(select(func.count()).select_from(AttendanceImportBatch)) == 2
        assert db.scalar(select(func.count()).select_from(AttendancePunch)) == 4
        assert db.scalar(select(func.count()).select_from(AttendanceDailyRecord).where(
            AttendanceDailyRecord.status == "approved"
        )) == 1
        actions = set(db.scalars(select(AuditLog.action)).all())
        assert "attendance_import_created" in actions
        assert "attendance_day_approved" in actions
        assert "attendance_month_approved" in actions
        saved_program = db.get(AttendanceImportBatch, program_id)
        assert saved_program.original_file_bytes == program_bytes

    Base.metadata.drop_all(engine)
    engine.dispose()
