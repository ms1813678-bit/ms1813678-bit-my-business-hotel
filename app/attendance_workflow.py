"""Persistence boundary for parsed attendance evidence; approval is a separate HR action."""
from __future__ import annotations

import hashlib
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import AttendanceImportBatch, AttendancePunch, AuditLog


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
