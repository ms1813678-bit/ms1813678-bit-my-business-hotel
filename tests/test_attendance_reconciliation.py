from datetime import date, datetime, time

from app.attendance_reconciliation import (
    build_monthly_reconciliation_proposals,
    infer_non_security_shift,
    reconcile_attendance_day,
    resolve_security_shift,
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


def test_security_shift_uses_roster_assignment_even_when_start_time_is_ambiguous():
    patterns = [
        pattern("security-12h", "08:00", "20:00"),
        pattern("security-8h", "08:00", "16:00"),
    ]
    patterns[0]["id"] = 10
    patterns[1]["id"] = 11
    result = resolve_security_shift(
        7,
        date(2026, 11, 1),
        {
            "employee_id": 7,
            "work_date": date(2026, 11, 1),
            "shift_pattern_id": 11,
        },
        patterns,
    )
    assert result["status"] == "ready_for_review"
    assert result["shift"]["name"] == "security-8h"


def test_security_attendance_without_roster_assignment_requires_review():
    result = resolve_security_shift(
        7, date(2026, 11, 1), None,
        [pattern("security-12h", "08:00", "20:00")],
    )
    assert result["status"] == "needs_review"
    assert result["reason"] == "security_roster_assignment_missing"


def test_monthly_builder_uses_fingerprint_shift_and_program_primary_values():
    pattern_data = pattern("reception-morning", "07:00", "15:00", 60, 60)
    pattern_data["id"] = 20
    result = build_monthly_reconciliation_proposals(
        employees=[{"id": 1, "department_id": 2}],
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 1),
        program_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 7, 5), "event_type": "IN"},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 15, 2), "event_type": "OUT"},
        ],
        fingerprint_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 7, 5)},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 15, 2)},
        ],
        patterns_by_department={2: [pattern_data]},
        security_assignments=[],
        security_department_id=99,
        security_approved_dates=set(),
    )
    assert len(result) == 1
    proposal = result[0]["proposal"]
    assert proposal["status"] == "ready_for_review"
    assert proposal["shift_pattern_id"] == 20
    assert proposal["worked_minutes"] == 477


def test_monthly_builder_keeps_overnight_punches_on_shift_start_work_date():
    night = pattern("night", "23:00", "07:00", 60, 60)
    night["id"] = 21
    result = build_monthly_reconciliation_proposals(
        employees=[{"id": 1, "department_id": 2}],
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 1),
        program_events=[],
        fingerprint_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 23, 5)},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 2, 7, 3)},
        ],
        patterns_by_department={2: [night]},
        security_assignments=[],
        security_department_id=99,
        security_approved_dates=set(),
    )
    assert len(result) == 1
    proposal = result[0]["proposal"]
    assert proposal["work_date"] == date(2026, 11, 1)
    assert proposal["scheduled_end_at"] == datetime(2026, 11, 2, 7, 0)
    assert proposal["worked_minutes"] == 478


def test_monthly_builder_security_uses_roster_and_omits_approved_off_day():
    security_shift = pattern("security-night", "20:00", "08:00")
    security_shift["id"] = 30
    result = build_monthly_reconciliation_proposals(
        employees=[{"id": 7, "department_id": 99}],
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 2),
        program_events=[],
        fingerprint_events=[
            {"employee_id": 7, "punch_at": datetime(2026, 11, 1, 20, 0)},
            {"employee_id": 7, "punch_at": datetime(2026, 11, 2, 8, 0)},
        ],
        patterns_by_department={99: [security_shift]},
        security_assignments=[{
            "id": 50, "employee_id": 7, "work_date": date(2026, 11, 1),
            "shift_pattern_id": 30,
        }],
        security_department_id=99,
        security_approved_dates={date(2026, 11, 1), date(2026, 11, 2)},
    )
    assert len(result) == 1
    assert result[0]["security_shift_assignment_id"] == 50
    assert result[0]["proposal"]["scheduled_end_at"] == datetime(2026, 11, 2, 8, 0)


def test_monthly_builder_does_not_invent_missing_attendance():
    result = build_monthly_reconciliation_proposals(
        employees=[{"id": 1, "department_id": 2}],
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 1),
        program_events=[],
        fingerprint_events=[],
        patterns_by_department={2: [pattern("day", "08:00", "16:00")]},
        security_assignments=[],
        security_department_id=99,
        security_approved_dates=set(),
    )
    assert result == []


def test_monthly_builder_groups_program_overnight_clock_out_on_shift_start_date():
    night = pattern("night-program", "23:00", "07:00", 60, 60)
    night["id"] = 41
    result = build_monthly_reconciliation_proposals(
        employees=[{"id": 1, "department_id": 2}],
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 2),
        program_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 23, 5), "event_type": "IN"},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 2, 7, 3), "event_type": "OUT"},
        ],
        fingerprint_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 23, 5)},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 2, 7, 3)},
        ],
        patterns_by_department={2: [night]},
        security_assignments=[],
        security_department_id=99,
        security_approved_dates=set(),
    )
    assert len(result) == 1
    proposal = result[0]["proposal"]
    assert proposal["work_date"] == date(2026, 11, 1)
    assert proposal["program_clock_in"] == datetime(2026, 11, 1, 23, 5)
    assert proposal["program_clock_out"] == datetime(2026, 11, 2, 7, 3)
    assert proposal["worked_minutes"] == 478


def test_duplicate_fingerprint_timestamp_requires_review():
    result = reconcile_attendance_day(
        work_date=date(2026, 11, 1),
        shift_pattern=pattern("day", "08:00", "16:00"),
        program_clock_in=None,
        program_clock_out=None,
        fingerprint_punches=[
            datetime(2026, 11, 1, 8, 0),
            datetime(2026, 11, 1, 8, 0),
            datetime(2026, 11, 1, 16, 0),
        ],
    )
    assert result["status"] == "needs_review"
    assert "duplicate_fingerprint_events" in result["issues"]


def test_overnight_program_and_fingerprint_events_do_not_create_next_day_duplicate():
    night = pattern("night-no-duplicate", "23:00", "07:00", 60, 60)
    night["id"] = 42
    result = build_monthly_reconciliation_proposals(
        employees=[{"id": 1, "department_id": 2}],
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 2),
        program_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 23, 5), "event_type": "IN"},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 2, 7, 3), "event_type": "OUT"},
        ],
        fingerprint_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 23, 5)},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 2, 3, 0)},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 2, 7, 3)},
        ],
        patterns_by_department={2: [night]},
        security_assignments=[],
        security_department_id=99,
        security_approved_dates=set(),
    )
    assert len(result) == 1
    proposal = result[0]["proposal"]
    assert proposal["work_date"] == date(2026, 11, 1)
    assert proposal["program_clock_out"] == datetime(2026, 11, 2, 7, 3)


def test_multiple_program_clock_in_events_require_review():
    night = pattern("day-multiple-in", "08:00", "16:00", 60, 60)
    night["id"] = 61
    result = build_monthly_reconciliation_proposals(
        employees=[{"id": 1, "department_id": 2}],
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 1),
        program_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 8, 0), "event_type": "IN"},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 8, 7), "event_type": "IN"},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 16, 0), "event_type": "OUT"},
        ],
        fingerprint_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 8, 0)},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 16, 0)},
        ],
        patterns_by_department={2: [night]},
        security_assignments=[],
        security_department_id=99,
        security_approved_dates=set(),
    )
    assert len(result) == 1
    proposal = result[0]["proposal"]
    assert proposal["status"] == "needs_review"
    assert "multiple_program_clock_in_events" in proposal["issues"]


def test_multiple_program_clock_out_events_require_review():
    day = pattern("day-multiple-out", "08:00", "16:00", 60, 60)
    day["id"] = 62
    result = build_monthly_reconciliation_proposals(
        employees=[{"id": 1, "department_id": 2}],
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 1),
        program_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 8, 0), "event_type": "IN"},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 16, 0), "event_type": "OUT"},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 16, 8), "event_type": "OUT"},
        ],
        fingerprint_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 8, 0)},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 16, 0)},
        ],
        patterns_by_department={2: [day]},
        security_assignments=[],
        security_department_id=99,
        security_approved_dates=set(),
    )
    assert len(result) == 1
    proposal = result[0]["proposal"]
    assert proposal["status"] == "needs_review"
    assert "multiple_program_clock_out_events" in proposal["issues"]


def test_next_day_program_event_after_non_overnight_shift_is_reviewed_without_crashing():
    evening = pattern("evening", "15:00", "23:00", 60, 60)
    evening["id"] = 71
    result = build_monthly_reconciliation_proposals(
        employees=[{"id": 1, "department_id": 2}],
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 2),
        program_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 15, 5), "event_type": "IN"},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 23, 0), "event_type": "OUT"},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 2, 0, 30), "event_type": "IN"},
        ],
        fingerprint_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 15, 5)},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 23, 0)},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 2, 0, 30)},
        ],
        patterns_by_department={2: [evening]},
        security_assignments=[],
        security_department_id=99,
        security_approved_dates=set(),
    )
    by_date = {item["proposal"]["work_date"]: item["proposal"] for item in result}
    assert by_date[date(2026, 11, 1)]["last_clock_out"] == datetime(2026, 11, 1, 23, 0)
    assert by_date[date(2026, 11, 2)]["status"] == "needs_review"
    assert by_date[date(2026, 11, 2)]["program_clock_in"] == datetime(2026, 11, 2, 0, 30)


def test_next_day_fingerprint_is_not_attached_to_non_overnight_15_to_23_shift():
    evening = pattern("evening", "15:00", "23:00", 60, 60)
    evening["id"] = 70
    result = build_monthly_reconciliation_proposals(
        employees=[{"id": 1, "department_id": 2}],
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 2),
        program_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 15, 5), "event_type": "IN"},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 23, 0), "event_type": "OUT"},
        ],
        fingerprint_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 15, 5)},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 23, 0)},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 2, 0, 30)},
        ],
        patterns_by_department={2: [evening]},
        security_assignments=[],
        security_department_id=99,
        security_approved_dates=set(),
    )
    by_date = {item["proposal"]["work_date"]: item["proposal"] for item in result}
    assert set(by_date) == {date(2026, 11, 1), date(2026, 11, 2)}
    assert by_date[date(2026, 11, 1)]["last_clock_out"] == datetime(2026, 11, 1, 23, 0)
    assert by_date[date(2026, 11, 2)]["shift_pattern_id"] is None
    assert by_date[date(2026, 11, 2)]["status"] == "needs_review"



def test_monthly_builder_respects_service_period_start_and_end_dates():
    day = pattern("day", "08:00", "16:00", 60, 60)
    day["id"] = 91
    result = build_monthly_reconciliation_proposals(
        employees=[{
            "id": 1,
            "department_id": 2,
            "hire_date": date(2026, 11, 1),
            "service_periods": [{
                "start_date": date(2026, 11, 2),
                "end_date": date(2026, 11, 3),
            }],
        }],
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 4),
        program_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 8, 0), "event_type": "IN"},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 2, 8, 0), "event_type": "IN"},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 3, 16, 0), "event_type": "OUT"},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 4, 8, 0), "event_type": "IN"},
        ],
        fingerprint_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 2, 8, 0)},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 3, 16, 0)},
        ],
        patterns_by_department={2: [day]},
        security_assignments=[],
        security_department_id=99,
        security_approved_dates=set(),
    )
    assert {item["proposal"]["work_date"] for item in result} == {
        date(2026, 11, 2), date(2026, 11, 3)
    }



def test_previous_overnight_shift_out_is_not_misclassified_as_next_day_shift_in():
    night = pattern("night", "23:00", "07:00", 120, 120)
    night["id"] = 101
    morning = pattern("morning", "07:00", "15:00", 120, 120)
    morning["id"] = 102
    result = build_monthly_reconciliation_proposals(
        employees=[{"id": 1, "department_id": 2}],
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 2),
        program_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 23, 0), "event_type": "IN"},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 2, 7, 3), "event_type": "OUT"},
        ],
        fingerprint_events=[
            {"employee_id": 1, "punch_at": datetime(2026, 11, 1, 23, 0)},
            {"employee_id": 1, "punch_at": datetime(2026, 11, 2, 7, 3)},
        ],
        patterns_by_department={2: [night, morning]},
        security_assignments=[],
        security_department_id=99,
        security_approved_dates=set(),
    )
    assert len(result) == 1
    assert result[0]["proposal"]["work_date"] == date(2026, 11, 1)
    assert result[0]["proposal"]["scheduled_end_at"] == datetime(2026, 11, 2, 7, 0)
