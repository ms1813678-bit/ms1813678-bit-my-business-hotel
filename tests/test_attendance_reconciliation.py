from datetime import date, datetime, time

from app.attendance_reconciliation import (
    infer_non_security_shift,
    reconcile_attendance_day,
    shift_window,
    work_date_for_punch,
)


def pattern(identifier, start, end, before=120, after=120):
    return {
        "id": identifier,
        "name": identifier,
        "start_time": time.fromisoformat(start),
        "end_time": time.fromisoformat(end),
        "match_before_minutes": before,
        "match_after_minutes": after,
    }


def test_overnight_shift_window_crosses_to_next_calendar_day():
    start, end = shift_window(date(2026, 11, 1), pattern("night", "23:00", "07:00"))
    assert start == datetime(2026, 11, 1, 23, 0)
    assert end == datetime(2026, 11, 2, 7, 0)


def test_punch_after_midnight_belongs_to_previous_shift_start_date():
    assert work_date_for_punch(
        datetime(2026, 11, 2, 0, 25), time(23, 0)
    ) == date(2026, 11, 1)


def test_late_evening_punch_before_midnight_belongs_to_next_midnight_shift():
    assert work_date_for_punch(
        datetime(2026, 11, 1, 23, 55), time(0, 0)
    ) == date(2026, 11, 2)


def test_first_clock_in_matches_only_configured_department_shift():
    patterns = [
        pattern("morning", "07:00", "15:00", 60, 60),
        pattern("evening", "15:00", "23:00", 60, 60),
    ]
    result = infer_non_security_shift(datetime(2026, 11, 1, 7, 8), patterns)
    assert result["status"] == "ready_for_review"
    assert result["shift"]["id"] == "morning"
    assert result["work_date"] == date(2026, 11, 1)


def test_ambiguous_or_unmatched_first_punch_requires_review():
    result = infer_non_security_shift(
        datetime(2026, 11, 1, 8, 0),
        [
            pattern("shift-a", "08:00", "16:00"),
            pattern("shift-b", "08:00", "20:00"),
        ],
    )
    assert result["status"] == "needs_review"
    assert result["shift"] is None


def test_program_report_is_primary_and_fingerprint_fills_missing_clock_out():
    schedule = pattern("night", "23:00", "07:00")
    result = reconcile_attendance_day(
        work_date=date(2026, 11, 1),
        shift_pattern=schedule,
        program_clock_in=datetime(2026, 11, 1, 23, 5),
        program_clock_out=None,
        fingerprint_punches=[
            datetime(2026, 11, 1, 23, 5),
            datetime(2026, 11, 2, 7, 3),
        ],
    )
    assert result["status"] == "ready_for_review"
    assert result["selected_clock_in"] == datetime(2026, 11, 1, 23, 5)
    assert result["selected_clock_out"] == datetime(2026, 11, 2, 7, 3)
    assert result["worked_minutes"] == 478


def test_source_disagreement_is_preserved_and_requires_hr_review():
    result = reconcile_attendance_day(
        work_date=date(2026, 11, 1),
        shift_pattern=pattern("day", "08:00", "16:00"),
        program_clock_in=datetime(2026, 11, 1, 8, 0),
        program_clock_out=datetime(2026, 11, 1, 16, 0),
        fingerprint_punches=[
            datetime(2026, 11, 1, 8, 12),
            datetime(2026, 11, 1, 16, 4),
        ],
    )
    assert result["status"] == "needs_review"
    assert result["program_clock_in"] == datetime(2026, 11, 1, 8, 0)
    assert result["first_clock_in"] == datetime(2026, 11, 1, 8, 12)
    assert "program_fingerprint_clock_in_mismatch" in result["issues"]


def test_missing_clock_event_is_not_fabricated():
    result = reconcile_attendance_day(
        work_date=date(2026, 11, 1),
        shift_pattern=pattern("day", "08:00", "16:00"),
        program_clock_in=None,
        program_clock_out=None,
        fingerprint_punches=[datetime(2026, 11, 1, 8, 0)],
    )
    assert result["status"] == "needs_review"
    assert result["worked_minutes"] is None
    assert "only_one_fingerprint_event" in result["issues"]
