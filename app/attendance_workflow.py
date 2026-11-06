"""Persistence boundary for parsed attendance evidence; approval is a separate HR action."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.models import (AttendanceDailyRecord, AttendanceImportBatch, AttendanceImportIssue, AttendanceMonth, AttendancePunch, AuditLog, Department, Employee, EmployeeChange, EmployeeServicePeriod, SecurityShiftAssignment, ShiftPattern)


def _department_history_by_date(db, employees, period_start: date, period_end: date):
    """Resolve each employee's effective department for every day in a period."""
    employee_ids = [employee.id for employee in employees]
    changes = db.scalars(
        select(EmployeeChange).where(
            EmployeeChange.employee_id.in_(employee_ids),
            EmployeeChange.field_name == "department_id",
        ).order_by(
            EmployeeChange.effective_date.desc(), EmployeeChange.id.desc()
        )
    ).all() if employee_ids else []
    by_employee = {}
    for change in changes:
        by_employee.setdefault(change.employee_id, []).append(change)

    result = {}
    for employee in employees:
        history = by_employee.get(employee.id, [])
        daily_departments = {}
        current = employee.department_id
        work_date = period_start
        while work_date <= period_end:
            department_id = current
            for change in history:
                if change.effective_date <= work_date:
                    continue
                try:
                    expected_new = int(change.new_value)
                    previous_department = int(change.old_value)
                except (TypeError, ValueError):
                    raise ValueError("employee_department_history_is_incomplete")
                if department_id != expected_new:
                    raise ValueError("employee_department_history_is_inconsistent")
                department_id = previous_department
            daily_departments[work_date] = department_id
            work_date += timedelta(days=1)
        result[employee.id] = daily_departments
    return result


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

    # The program report defines the monthly period. A fingerprint export may
    # contain only dates with recorded punches, so align a contained fingerprint
    # range to the one already-imported program month instead of treating it as
    # a shorter, separate month.
    if source_type == "fingerprint":
        matching_program_months = db.scalars(
            select(AttendanceMonth).where(
                AttendanceMonth.program_import_batch_id.is_not(None),
                AttendanceMonth.period_start <= period_start,
                AttendanceMonth.period_end >= period_end,
            ).with_for_update()
        ).all()
        if len(matching_program_months) > 1:
            raise ValueError("fingerprint_import_period_matches_multiple_program_months")
        if matching_program_months:
            period_start = matching_program_months[0].period_start
            period_end = matching_program_months[0].period_end

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
        original_file_bytes=bytes(file_bytes),
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
    previous_batch_id = getattr(month, batch_field)
    if previous_batch_id is not None:
        previous_batch = db.get(AttendanceImportBatch, previous_batch_id)
        if previous_batch is None or previous_batch.status != "needs_review":
            raise ValueError("attendance_month_source_already_imported")
        previous_batch.status = "rejected"
        db.add(AuditLog(
            actor=actor,
            action="attendance_import_batch_replaced",
            entity_type="attendance_import_batch",
            entity_id=str(previous_batch.id),
            old_value=f"status=needs_review; source={source_type}",
            new_value=f"status=rejected; replacement_batch={batch.id}",
            reason="HR replaced an unresolved source import before monthly reconciliation",
        ))
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
    if (
        program_batch.status != "parsed" or fingerprint_batch.status != "parsed"
        or program_batch.issue_count or fingerprint_batch.issue_count
    ):
        raise ValueError("attendance_import_batch_has_unresolved_issues")
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

    # Include former employees whose current hire_date reflects a later
    # rehire; their earlier EmployeeServicePeriod may still overlap this month.
    all_employees = db.scalars(select(Employee).order_by(Employee.id)).all()
    # Load the full service history so an employee with only a future
    # rehire/start period is not incorrectly treated as active in this month.
    service_period_rows = db.scalars(
        select(EmployeeServicePeriod).order_by(
            EmployeeServicePeriod.employee_id, EmployeeServicePeriod.start_date
        )
    ).all()
    service_periods_by_employee = {}
    for service_period in service_period_rows:
        service_periods_by_employee.setdefault(service_period.employee_id, []).append({
            "start_date": service_period.start_date,
            "end_date": service_period.end_date,
        })
    employees = [
        employee for employee in all_employees
        if (
            any(
                period["start_date"] <= period_end
                and (period["end_date"] is None or period["end_date"] >= period_start)
                for period in service_periods_by_employee.get(employee.id, [])
            )
            if service_periods_by_employee.get(employee.id)
            else employee.status == "active"
        )
    ]
    if not employees:
        raise ValueError("no_employees_in_attendance_service_period")
    department_history_by_employee = _department_history_by_date(
        db, employees, period_start, period_end
    )

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
            "service_periods": service_periods_by_employee.get(item.id, []),
            "department_by_date": department_history_by_employee[item.id],
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
    if (
        batches[0].source_type != "program_report"
        or batches[1].source_type != "fingerprint"
        or any(
            batch.period_start != month.period_start or batch.period_end != month.period_end
            for batch in batches
        )
    ):
        raise ValueError("attendance_month_source_batch_mismatch")
    if any(batch.status != "parsed" for batch in batches):
        raise ValueError("attendance_month_source_batch_not_ready_for_approval")
    if any(batch.issue_count for batch in batches):
        raise ValueError("attendance_month_has_unresolved_import_issues")

    rows = db.scalars(
        select(AttendanceDailyRecord).where(
            AttendanceDailyRecord.work_date >= month.period_start,
            AttendanceDailyRecord.work_date <= month.period_end,
            AttendanceDailyRecord.program_import_batch_id == month.program_import_batch_id,
            AttendanceDailyRecord.fingerprint_import_batch_id == month.fingerprint_import_batch_id,
        ).with_for_update()
    ).all()
    if not rows:
        raise ValueError("attendance_month_has_no_daily_records")
    if any(row.status != "approved" for row in rows):
        raise ValueError("attendance_month_has_unapproved_daily_records")

    coverage_summary = summarize_attendance_month_coverage(db, month)
    old_status = month.status
    month.status = "approved"
    for batch in batches:
        batch.status = "approved"
        batch.reviewed_by = actor
        batch.reviewed_at = datetime.now(timezone.utc)
    month.approved_by = actor
    month.approved_at = datetime.now(timezone.utc)
    month.approval_reason = reason.strip()
    db.add(AuditLog(
        actor=actor,
        action="attendance_month_approved",
        entity_type="attendance_month",
        entity_id=str(month.id),
        old_value=f"status={old_status}; daily_records={len(rows)}",
        new_value=(
            f"status=approved; daily_records={len(rows)}; "
            f"program_batch={batches[0].id}; fingerprint_batch={batches[1].id}; "
            "source_batches=approved; coverage_confirmed=true; "
            f"unrepresented_employee_days={coverage_summary['unrepresented_employee_days']}"
        ),
        reason=reason.strip(),
    ))
    db.flush()
    return month



def resolve_unknown_attendance_employee(
    db, *, issue_id: int, employee_id: int, actor: str
):
    """Resolve one unknown-employee source row without changing its raw evidence."""
    if not actor or not actor.strip():
        raise ValueError("attendance_issue_resolving_admin_required")
    issue = db.scalar(
        select(AttendanceImportIssue)
        .where(AttendanceImportIssue.id == issue_id)
        .with_for_update()
    )
    if issue is None:
        raise ValueError("attendance_import_issue_not_found")
    if issue.reason != "unknown_employee":
        raise ValueError("attendance_import_issue_not_identity_resolvable")
    if issue.resolved_at is not None:
        raise ValueError("attendance_import_issue_already_resolved")
    batch = db.get(AttendanceImportBatch, issue.import_batch_id)
    if batch is None or batch.status != "needs_review":
        raise ValueError("attendance_import_batch_not_open_for_issue_resolution")
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise ValueError("attendance_employee_not_found")

    source_punches = db.scalars(
        select(AttendancePunch).where(
            AttendancePunch.import_batch_id == batch.id,
            AttendancePunch.source_sheet == issue.source_sheet,
            AttendancePunch.source_row == issue.source_row,
            AttendancePunch.employee_id.is_(None),
        ).with_for_update()
    ).all()
    if not source_punches:
        raise ValueError("attendance_unknown_employee_source_punches_not_found")

    for punch in source_punches:
        punch.employee_id = employee.id
    issue.resolved_employee_id = employee.id
    issue.resolved_by = actor.strip()
    issue.resolved_at = datetime.now(timezone.utc)
    # SessionLocal disables autoflush; persist the resolution before counting
    # remaining unresolved issues in this batch.
    db.flush()

    unresolved_count = db.scalar(
        select(func.count()).select_from(AttendanceImportIssue).where(
            AttendanceImportIssue.import_batch_id == batch.id,
            AttendanceImportIssue.resolved_at.is_(None),
        )
    ) or 0
    batch.issue_count = unresolved_count
    batch.status = "parsed" if unresolved_count == 0 else "needs_review"
    db.add(AuditLog(
        actor=actor.strip(),
        action="attendance_import_unknown_employee_resolved",
        entity_type="attendance_import_issue",
        entity_id=str(issue.id),
        old_value=f"raw_value={issue.raw_value}; unresolved_punches={len(source_punches)}",
        new_value=f"employee_id={employee.id}; unresolved_issues={unresolved_count}",
        reason="HR explicitly matched an unknown source identity; raw source values retained",
    ))
    db.flush()
    return issue



def summarize_attendance_month_coverage(db, month: AttendanceMonth):
    """List employee-days without a generated record or an approved Security roster.

    These are coverage gaps for HR to inspect against both source workbooks. They
    are never converted to absence or rest-day classifications automatically.
    """
    # Do not filter by Employee.hire_date here: re-hire updates that field,
    # while EmployeeServicePeriod retains earlier historical employment periods.
    employees = db.scalars(select(Employee).order_by(Employee.id)).all()
    service_rows = db.scalars(
        select(EmployeeServicePeriod).order_by(
            EmployeeServicePeriod.employee_id, EmployeeServicePeriod.start_date
        )
    ).all()
    service_by_employee = {}
    for row in service_rows:
        service_by_employee.setdefault(row.employee_id, []).append(row)
    department_by_employee_date = _department_history_by_date(
        db, employees, month.period_start, month.period_end
    )

    daily_rows = db.scalars(
        select(AttendanceDailyRecord).where(
            AttendanceDailyRecord.work_date >= month.period_start,
            AttendanceDailyRecord.work_date <= month.period_end,
            AttendanceDailyRecord.program_import_batch_id == month.program_import_batch_id,
            AttendanceDailyRecord.fingerprint_import_batch_id == month.fingerprint_import_batch_id,
        )
    ).all()
    represented = {(row.employee_id, row.work_date) for row in daily_rows}

    security_department = db.scalar(
        select(Department).where(
            Department.name == "Security", Department.active.is_(True)
        )
    )
    security_assignments = set()
    if security_department is not None:
        security_assignments = {
            (employee_id, work_date)
            for employee_id, work_date in db.execute(
                select(
                    SecurityShiftAssignment.employee_id,
                    SecurityShiftAssignment.work_date,
                ).where(
                    SecurityShiftAssignment.work_date >= month.period_start,
                    SecurityShiftAssignment.work_date <= month.period_end,
                )
            ).all()
        }

    security_approved_dates = set()
    approved_values = db.scalars(
        select(AuditLog.entity_id).where(
            AuditLog.action == "security_roster_day_approved",
            AuditLog.entity_type == "security_roster",
        )
    ).all()
    for raw in approved_values:
        try:
            day = date.fromisoformat(raw)
        except (TypeError, ValueError):
            continue
        if month.period_start <= day <= month.period_end:
            security_approved_dates.add(day)

    gaps = []
    for employee in employees:
        periods = service_by_employee.get(employee.id, [])
        if periods:
            service_dates = set()
            for period in periods:
                first = max(period.start_date, month.period_start)
                last = min(period.end_date or month.period_end, month.period_end)
                current = first
                while current <= last:
                    service_dates.add(current)
                    current += timedelta(days=1)
        elif employee.status == "active":
            first = max(employee.hire_date, month.period_start)
            service_dates = set()
            current = first
            while current <= month.period_end:
                service_dates.add(current)
                current += timedelta(days=1)
        else:
            service_dates = set()

        for work_date in sorted(service_dates):
            if (employee.id, work_date) in represented:
                continue
            if (
                security_department is not None
                and department_by_employee_date[employee.id][work_date] == security_department.id
                and work_date in security_approved_dates
                and (employee.id, work_date) not in security_assignments
            ):
                # On an approved Security roster, no assignment means OFF.
                # Employees with an assignment still require attendance review.
                continue
            gaps.append({
                "employee_id": employee.id,
                "employee_code": employee.code,
                "employee_name": employee.full_name,
                "work_date": work_date,
            })

    return {
        "unrepresented_employee_days": len(gaps),
        "coverage_examples": gaps[:20],
    }
