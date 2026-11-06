"""Conservative attendance reconciliation proposals; never approves payroll data."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.shift_matching import match_shift_by_clock


def shift_window(work_date: date, pattern: dict) -> tuple[datetime, datetime]:
    start_time = pattern.get("start_time")
    end_time = pattern.get("end_time")
    if not isinstance(start_time, time) or not isinstance(end_time, time):
        raise ValueError("shift_pattern_requires_start_and_end_times")
    start = datetime.combine(work_date, start_time)
    end = datetime.combine(work_date, end_time)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def work_date_for_punch(punch_at: datetime, shift_start: time) -> date:
    """Assign a midnight-crossing punch to the date on which its shift started."""
    observed = punch_at.time()
    if observed < shift_start and (shift_start.hour * 60 + shift_start.minute
                                   - observed.hour * 60 - observed.minute) > 12 * 60:
        return punch_at.date() - timedelta(days=1)
    if observed > shift_start and (observed.hour * 60 + observed.minute
                                   - shift_start.hour * 60 - shift_start.minute) > 12 * 60:
        return punch_at.date() + timedelta(days=1)
    return punch_at.date()


def infer_non_security_shift(first_clock_in: datetime, patterns: list[dict]) -> dict:
    """Use only the first observed clock-in and configured department patterns."""
    matched = match_shift_by_clock(first_clock_in, patterns)
    if matched["status"] != "matched":
        return {
            "status": "needs_review",
            "shift": None,
            "work_date": None,
            "reason": matched["reason"],
        }
    pattern = matched["shift"]
    return {
        "status": "ready_for_review",
        "shift": pattern,
        "work_date": work_date_for_punch(first_clock_in, pattern["start_time"]),
        "reason": None,
    }


def reconcile_attendance_day(
    *,
    work_date: date,
    shift_pattern: dict,
    program_clock_in: datetime | None,
    program_clock_out: datetime | None,
    fingerprint_punches: list[datetime],
) -> dict:
    """Prepare a daily comparison, preserving both sources and surfacing differences.

    The program report is primary. Fingerprint evidence fills missing endpoints only.
    A disagreement is not silently resolved; HR receives both values for review.
    """
    scheduled_start, scheduled_end = shift_window(work_date, shift_pattern)
    evidence_start = scheduled_start - timedelta(minutes=120)
    evidence_end = scheduled_end + timedelta(minutes=120)
    relevant = sorted(
        punch for punch in fingerprint_punches
        if evidence_start <= punch <= evidence_end
    )
    first_fingerprint = relevant[0] if relevant else None
    last_fingerprint = relevant[-1] if len(relevant) >= 2 else None

    selected_in = program_clock_in or first_fingerprint
    selected_out = program_clock_out or last_fingerprint
    issues = []
    if len(relevant) == 1 and (program_clock_in is None or program_clock_out is None):
        issues.append("only_one_fingerprint_event")
    if not relevant and (program_clock_in is None or program_clock_out is None):
        issues.append("missing_fingerprint_evidence")
    if program_clock_in and first_fingerprint and program_clock_in != first_fingerprint:
        issues.append("program_fingerprint_clock_in_mismatch")
    if program_clock_out and last_fingerprint and program_clock_out != last_fingerprint:
        issues.append("program_fingerprint_clock_out_mismatch")

    if selected_out is not None and selected_in is not None and selected_out <= selected_in:
        if scheduled_end.date() > scheduled_start.date() and selected_out.date() == work_date:
            selected_out = selected_out + timedelta(days=1)
        else:
            issues.append("clock_out_not_after_clock_in")

    worked_minutes = None
    if selected_in is not None and selected_out is not None and selected_out > selected_in:
        worked_minutes = int((selected_out - selected_in).total_seconds() // 60)
    else:
        issues.append("incomplete_attendance_endpoints")

    return {
        "work_date": work_date,
        "shift_pattern_id": shift_pattern.get("id"),
        "scheduled_start_at": scheduled_start,
        "scheduled_end_at": scheduled_end,
        "program_clock_in": program_clock_in,
        "program_clock_out": program_clock_out,
        "first_clock_in": first_fingerprint,
        "last_clock_out": last_fingerprint,
        "selected_clock_in": selected_in,
        "selected_clock_out": selected_out,
        "worked_minutes": worked_minutes,
        "issues": sorted(set(issues)),
        "status": "ready_for_review" if not issues else "needs_review",
    }
