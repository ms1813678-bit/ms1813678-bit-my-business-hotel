"""Persistence boundary for parsed attendance evidence; approval is a separate HR action."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (AttendanceDailyRecord, AttendanceImportBatch, AttendanceImportIssue, AttendanceMonth, AttendancePunch, AuditLog, Department, Employee, SecurityShiftAssignment, ShiftPattern)


def persist_attendance_import(db, *, file_bytes: bytes, filename: str, parsed: dict, actor: str):
    """Persist a parsed source batch and its raw-positioned events atomically.

    The caller owns the transaction. Duplicate source files are rejected by both a
    pre-check and a database unique constraint. No daily attendance is approved here.
    """
    if not file_bytes:
        raise ValueError("empty_attendance_file")
    period_start = parsed.get("period_start")
    period_end = parsed.get("period_end")
    if period_start is None or period_end is None:
        raise ValueError("attendance_import_has_no_valid_dated_events")
    if period_end < period_start:
        raise ValueError("attendance_import_period_is_invalid")
    source_type = parsed.get("source_type")
    if source_type not in {"program_report", "fingerprint"}:
        raise ValueError("unsupported_attendance_source_type")

    digest = hashlib.sha256(file_bytes).hexdigest()
    duplicate = db.scalar(
        select(AttendanceImportBatch).where(
            AttendanceImportBatch.source_type == source_type,
            AttendanceImportBatch.file_sha256 == digest,
        )
    )
    if duplicate is not None:
        raise ValueError("attendance_source_file_already_imported")

    events = parsed.get("events", [])
    issues = parsed.get("issues", [])
    batch = AttendanceImportBatch(
        source_type=source_type,
        period_start=period_start,
        period_end=period_end,
        original_filename=(filename or "attendance.xlsx")[:255],
        file_sha256=digest,
        uploaded_by=actor,
        status="needs_review" if issues else "parsed",
        row_count=len(events),
        issue_count=len(issues),
    )
    db.add(batch)
    db.flush()

    month = db.scalar(
        select(AttendanceMonth).where(
            AttendanceMonth.period_start == period_start,
            AttendanceMonth.period_end == period_end,
        ).with_for_update()
    )
    if month is None:
        month = AttendanceMonth(
            period_start=period_start, period_end=period_end,
            status="imported", created_by=actor,
        )
        db.add(month)
        db.flush()
    if month.status != "imported":
        raise ValueError("attendance_month_already_under_review")
    batch_field = (
        "program_import_batch_id" if source_type == "program_report"
        else "fingerprint_import_batch_id"
    )
    if getattr(month, batch_field) is not None:
        raise ValueError("attendance_month_source_already_imported")
    setattr(month, batch_field, batch.id)

    for event in events:
        db.add(AttendancePunch(
            import_batch_id=batch.id,
            employee_id=event.get("employee_id"),
            raw_employee_code=event.get("raw_employee_code"),
            raw_employee_name=event.get("raw_employee_name"),
            punch_at=event.get("punch_at"),
            raw_timestamp=str(event.get("raw_timestamp", ""))[:120],
            event_type=event.get("event_type"),
            source_sheet=str(event.get("source_sheet", "Sheet1"))[:120],
            source_row=int(event["source_row"]),
            event_index=int(event["event_index"]),
        ))
    for issue in issues:
        db.add(AttendanceImportIssue(
            import_batch_id=batch.id,
            source_sheet=str(issue.get("sheet"))[:120] if issue.get("sheet") is not None else None,
            source_row=issue.get("row"),
            reason=str(issue.get("reason", "unknown_import_issue"))[:100],
            raw_value=str(issue.get("value"))[:2000] if issue.get("value") is not None else None,
            details=json.dumps(issue, ensure_ascii=False, default=str),
        ))
    db.add(AuditLog(
        actor=actor,
        action="attendance_import_created",
        entity_type="attendance_import_batch",
        entity_id=str(batch.id),
        new_value=(
            f"source={source_type}; sha256={digest}; events={len(events)}; "
            f"issues={len(issues)}; period={period_start.isoformat()}..{period_end.isoformat()}"
        ),
        reason="Imported attendance evidence; pending reconciliation and HR review",
    ))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ValueError("attendance_import_persistence_conflict")
    return batch


def persist_daily_attendance_proposal(
    db,
    *,
    employee_id: int,
    proposal: dict,
    actor: str,
    program_import_batch_id: int | None = None,
    fingerprint_import_batch_id: int | None = None,
    security_shift_assignment_id: int | None = None,
):
    """Store a non-approved daily reconciliation proposal for HR review."""
    if proposal.get("status") not in {"ready_for_review", "needs_review"}:
        raise ValueError("invalid_attendance_proposal_status")
    if proposal.get("work_date") is None:
        raise ValueError("attendance_proposal_work_date_required")
    record = AttendanceDailyRecord(
        employee_id=employee_id,
        work_date=proposal["work_date"],
        shift_pattern_id=proposal.get("shift_pattern_id"),
        security_shift_assignment_id=security_shift_assignment_id,
        program_import_batch_id=program_import_batch_id,
        fingerprint_import_batch_id=fingerprint_import_batch_id,
        scheduled_start_at=proposal.get("scheduled_start_at"),
        scheduled_end_at=proposal.get("scheduled_end_at"),
        program_clock_in=proposal.get("program_clock_in"),
        program_clock_out=proposal.get("program_clock_out"),
        first_clock_in=proposal.get("first_clock_in"),
        last_clock_out=proposal.get("last_clock_out"),
        worked_minutes=proposal.get("worked_minutes"),
        status=proposal["status"],
        review_notes="; ".join(proposal.get("issues", [])) or None,
    )
    db.add(record)
    db.flush()
    db.add(AuditLog(
        actor=actor,
        action="attendance_day_proposed",
        entity_type="attendance_daily_record",
        entity_id=f"{employee_id}:{proposal['work_date'].isoformat()}",
        new_value=(
            f"status={record.status}; worked_minutes={record.worked_minutes}; "
            f"issues={record.review_notes or 'none'}"
        ),
        reason="Attendance reconciliation proposal saved for HR review; not approved",
    ))
    db.flush()
    return record



def prepare_and_persist_monthly_reconciliation(
    db,
    *,
    program_batch_id: int,
    fingerprint_batch_id: int,
    period_start: date,
    period_end: date,
    actor: str,
) -> dict:
    """Build and persist one month of unapproved daily proposals from both source files."""
    from app.attendance_reconciliation import build_monthly_reconciliation_proposals

    if period_end < period_start:
        raise ValueError("attendance_period_is_invalid")
    program_batch = db.get(AttendanceImportBatch, program_batch_id)
    fingerprint_batch = db.get(AttendanceImportBatch, fingerprint_batch_id)
    if program_batch is None or program_batch.source_type != "program_report":
        raise ValueError("program_report_batch_not_found")
    if fingerprint_batch is None or fingerprint_batch.source_type != "fingerprint":
        raise ValueError("fingerprint_batch_not_found")
    if program_batch.status not in {"parsed", "needs_review"} or fingerprint_batch.status not in {"parsed", "needs_review"}:
        raise ValueError("attendance_import_batch_not_ready_for_reconciliation")
    if (
        program_batch.period_start != period_start or program_batch.period_end != period_end
        or fingerprint_batch.period_start != period_start or fingerprint_batch.period_end != period_end
    ):
        raise ValueError("attendance_import_period_mismatch")

    existing = db.scalar(
        select(AttendanceDailyRecord.id).where(
            AttendanceDailyRecord.work_date >= period_start,
            AttendanceDailyRecord.work_date <= period_end,
        ).limit(1)
    )
    if existing is not None:
        raise ValueError("attendance_period_already_has_reconciliation_records")

    month = db.scalar(
        select(AttendanceMonth).where(
            AttendanceMonth.period_start == period_start,
            AttendanceMonth.period_end == period_end,
        ).with_for_update()
    )
    if month is None:
        # Also support pre-existing import batches created before monthly tracking
        # was introduced, and controlled test/maintenance imports.
        month = AttendanceMonth(
            period_start=period_start, period_end=period_end,
            status="imported", created_by=actor,
            program_import_batch_id=program_batch_id,
            fingerprint_import_batch_id=fingerprint_batch_id,
        )
        db.add(month)
        db.flush()
    if month.status != "imported":
        raise ValueError("attendance_month_not_in_imported_state")
    if month.program_import_batch_id not in {None, program_batch_id}:
        raise ValueError("attendance_month_source_batch_mismatch")
    if month.fingerprint_import_batch_id not in {None, fingerprint_batch_id}:
        raise ValueError("attendance_month_source_batch_mismatch")
    month.program_import_batch_id = program_batch_id
    month.fingerprint_import_batch_id = fingerprint_batch_id

    employees = db.scalars(
        select(Employee).where(
            Employee.status == "active",
            Employee.hire_date <= period_end,
        ).order_by(Employee.id)
    ).all()
    if not employees:
        raise ValueError("no_active_employees_in_attendance_period")

    security_department = db.scalar(
        select(Department).where(
            Department.name == "Security",
            Department.active.is_(True),
        )
    )
    if security_department is None:
        raise ValueError("security_department_not_configured")

    patterns_by_department = {}
    for pattern in db.scalars(
        select(ShiftPattern).where(ShiftPattern.active.is_(True))
    ).all():
        patterns_by_department.setdefault(pattern.department_id, []).append({
            "id": pattern.id,
            "name": pattern.name,
            "start_time": pattern.start_time,
            "end_time": pattern.end_time,
            "match_before_minutes": pattern.match_before_minutes,
            "match_after_minutes": pattern.match_after_minutes,
        })

    assignments = db.scalars(
        select(SecurityShiftAssignment).where(
            SecurityShiftAssignment.work_date >= period_start,
            SecurityShiftAssignment.work_date <= period_end,
        )
    ).all()
    security_assignment_data = [{
        "id": item.id,
        "employee_id": item.employee_id,
        "work_date": item.work_date,
        "shift_pattern_id": item.shift_pattern_id,
    } for item in assignments]

    approved_rows = db.scalars(
        select(AuditLog.entity_id).where(
            AuditLog.action == "security_roster_day_approved",
            AuditLog.entity_type == "security_roster",
        )
    ).all()
    security_approved_dates = set()
    for raw_date in approved_rows:
        try:
            approved_date = date.fromisoformat(raw_date)
        except (TypeError, ValueError):
            continue
        if period_start <= approved_date <= period_end:
            security_approved_dates.add(approved_date)

    punches = db.scalars(
        select(AttendancePunch).where(
            AttendancePunch.import_batch_id.in_([program_batch_id, fingerprint_batch_id])
        ).order_by(AttendancePunch.punch_at, AttendancePunch.id)
    ).all()
    program_events, fingerprint_events = [], []
    for punch in punches:
        if punch.employee_id is None or punch.punch_at is None:
            continue
        item = {"employee_id": punch.employee_id, "punch_at": punch.punch_at,
                "event_type": punch.event_type}
        if punch.import_batch_id == program_batch_id:
            program_events.append(item)
        elif punch.import_batch_id == fingerprint_batch_id:
            fingerprint_events.append(item)

    proposals = build_monthly_reconciliation_proposals(
        employees=[{
            "id": item.id, "department_id": item.department_id,
            "hire_date": item.hire_date,
        } for item in employees],
        period_start=period_start,
        period_end=period_end,
        program_events=program_events,
        fingerprint_events=fingerprint_events,
        patterns_by_department=patterns_by_department,
        security_assignments=security_assignment_data,
        security_department_id=security_department.id,
        security_approved_dates=security_approved_dates,
    )
    if not proposals:
        raise ValueError("no_attendance_proposals_generated")

    import_issue_count = program_batch.issue_count + fingerprint_batch.issue_count
    if import_issue_count:
        for item in proposals:
            item["proposal"]["status"] = "needs_review"
            item["proposal"].setdefault("issues", []).append("source_import_has_unresolved_issues")

    ready_count = 0
    review_count = 0
    for item in proposals:
        persist_daily_attendance_proposal(
            db,
            employee_id=item["employee_id"],
            proposal=item["proposal"],
            actor=actor,
            program_import_batch_id=program_batch_id,
            fingerprint_import_batch_id=fingerprint_batch_id,
            security_shift_assignment_id=item.get("security_shift_assignment_id"),
        )
        if item["proposal"]["status"] == "ready_for_review":
            ready_count += 1
        else:
            review_count += 1

    month.status = "under_review"
    month.reviewed_by = actor
    month.reviewed_at = datetime.now(timezone.utc)
    db.add(AuditLog(
        actor=actor,
        action="attendance_month_reconciliation_created",
        entity_type="attendance_month",
        entity_id=f"{period_start.isoformat()}..{period_end.isoformat()}",
        new_value=f"proposals={len(proposals)}; ready={ready_count}; needs_review={review_count}",
        reason="Monthly attendance reconciliation created; pending HR review and approval",
    ))
    db.flush()
    return {
        "total": len(proposals),
        "ready_for_review": ready_count,
        "needs_review": review_count,
        "import_issue_count": import_issue_count,
        "period_start": period_start,
        "period_end": period_end,
    }


def approve_attendance_day(
    db,
    *,
    record_id: int,
    clock_in: datetime,
    clock_out: datetime,
    actor: str,
    reason: str,
):
    """Apply explicit HR approval while preserving the original imported evidence."""
    if not actor or not actor.strip():
        raise ValueError("attendance_approving_admin_required")
    if not isinstance(clock_in, datetime) or not isinstance(clock_out, datetime):
        raise ValueError("attendance_clock_values_must_be_datetimes")
    if clock_out <= clock_in:
        raise ValueError("attendance_clock_out_must_follow_clock_in")
    if not reason or not reason.strip():
        raise ValueError("attendance_approval_reason_required")

    record = db.scalar(
        select(AttendanceDailyRecord)
        .where(AttendanceDailyRecord.id == record_id)
        .with_for_update()
    )
    if record is None:
        raise ValueError("attendance_daily_record_not_found")
    if record.status == "approved":
        raise ValueError("attendance_daily_record_already_approved")
    if record.status not in {"ready_for_review", "needs_review"}:
        raise ValueError("attendance_daily_record_not_reviewable")

    old_value = (
        f"program_in={record.program_clock_in}; program_out={record.program_clock_out}; "
        f"fingerprint_in={record.first_clock_in}; fingerprint_out={record.last_clock_out}"
    )
    record.approved_clock_in = clock_in
    record.approved_clock_out = clock_out
    record.approved_by = actor
    record.approved_at = datetime.now(timezone.utc)
    record.status = "approved"
    record.review_notes = (
        f"{record.review_notes}; " if record.review_notes else ""
    ) + f"HR decision: {reason.strip()}"
    record.worked_minutes = int((clock_out - clock_in).total_seconds() // 60)

    db.add(AuditLog(
        actor=actor,
        action="attendance_day_approved",
        entity_type="attendance_daily_record",
        entity_id=str(record.id),
        old_value=old_value,
        new_value=f"clock_in={clock_in.isoformat()}; clock_out={clock_out.isoformat()}",
        reason=reason.strip(),
    ))
    db.flush()
    return record


def approve_attendance_month(
    db,
    *,
    month_id: int,
    actor: str,
    reason: str,
    coverage_confirmed: bool,
):
    """Close a month only after all daily rows and source issues are resolved.

    coverage_confirmed is an explicit HR attestation that all employee/day
    coverage gaps were checked; blank source cells are not absences.
    """
    if not actor or not actor.strip():
        raise ValueError("attendance_approving_admin_required")
    if not reason or not reason.strip():
        raise ValueError("attendance_month_approval_reason_required")
    if coverage_confirmed is not True:
        raise ValueError("attendance_month_coverage_confirmation_required")

    month = db.scalar(
        select(AttendanceMonth).where(AttendanceMonth.id == month_id).with_for_update()
    )
    if month is None:
        raise ValueError("attendance_month_not_found")
    if month.status == "approved":
        raise ValueError("attendance_month_already_approved")
    if month.status != "under_review":
        raise ValueError("attendance_month_not_under_review")
    if month.program_import_batch_id is None or month.fingerprint_import_batch_id is None:
        raise ValueError("attendance_month_sources_incomplete")

    batches = [
        db.get(AttendanceImportBatch, month.program_import_batch_id),
        db.get(AttendanceImportBatch, month.fingerprint_import_batch_id),
    ]
    if any(batch is None for batch in batches):
        raise ValueError("attendance_month_source_batch_missing")
    if any(batch.issue_count for batch in batches):
        raise ValueError("attendance_month_has_unresolved_import_issues")

    rows = db.scalars(
        select(AttendanceDailyRecord).where(
            AttendanceDailyRecord.work_date >= month.period_start,
            AttendanceDailyRecord.work_date <= month.period_end,
        ).with_for_update()
    ).all()
    if not rows:
        raise ValueError("attendance_month_has_no_daily_records")
    if any(row.status != "approved" for row in rows):
        raise ValueError("attendance_month_has_unapproved_daily_records")

    old_status = month.status
    month.status = "approved"
    month.approved_by = actor
    month.approved_at = datetime.now(timezone.utc)
    month.approval_reason = reason.strip()
    db.add(AuditLog(
        actor=actor,
        action="attendance_month_approved",
        entity_type="attendance_month",
        entity_id=str(month.id),
        old_value=f"status={old_status}; daily_records={len(rows)}",
        new_value=f"status=approved; daily_records={len(rows)}; coverage_confirmed=true",
        reason=reason.strip(),
    ))
    db.flush()
    return month
