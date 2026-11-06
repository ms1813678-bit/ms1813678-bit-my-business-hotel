"""Persistence boundary for parsed attendance evidence; approval is a separate HR action."""
from __future__ import annotations

import hashlib
import json
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import AttendanceDailyRecord, AttendanceImportBatch, AttendanceImportIssue, AttendancePunch, AuditLog


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
