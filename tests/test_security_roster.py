from datetime import time

from app.security_roster import build_security_day_proposal


PATTERNS = [
    {"id": 1, "start_time": time(8, 0), "end_time": time(20, 0)},
    {"id": 2, "start_time": time(20, 0), "end_time": time(8, 0)},
    {"id": 3, "start_time": time(0, 0), "end_time": time(8, 0)},
    {"id": 4, "start_time": time(8, 0), "end_time": time(16, 0)},
    {"id": 5, "start_time": time(16, 0), "end_time": time(0, 0)},
]


def test_two_working_security_employees_receive_12_hour_shifts():
    result = build_security_day_proposal(
        {"fayez": "OFF", "yasser": "20:00", "walid": "08:00"},
        PATTERNS,
    )
    assert result["status"] == "ready_for_admin_review"
    assert [(x["employee_id"], x["duration_hours"], x["shift_pattern_id"])
            for x in result["assignments"]] == [
        ("yasser", 12, 2), ("walid", 12, 1)
    ]


def test_three_working_security_employees_receive_8_hour_shifts():
    result = build_security_day_proposal(
        {"fayez": "00:00", "yasser": "16:00", "walid": "08:00"},
        PATTERNS,
    )
    assert result["status"] == "ready_for_admin_review"
    assert {x["duration_hours"] for x in result["assignments"]} == {8}
    assert {x["shift_pattern_id"] for x in result["assignments"]} == {3, 4, 5}


def test_midnight_is_a_shift_start_not_a_rest_day():
    result = build_security_day_proposal(
        {"fayez": "00:00", "yasser": "OFF", "walid": "08:00"},
        PATTERNS,
    )
    assert result["status"] == "ready_for_admin_review"
    fayez = next(x for x in result["assignments"] if x["employee_id"] == "fayez")
    assert fayez["end_time"] == time(12, 0)
    assert fayez["duration_hours"] == 12


def test_invalid_working_count_requires_review():
    result = build_security_day_proposal(
        {"fayez": "OFF", "yasser": "OFF", "walid": "08:00"},
        PATTERNS,
    )
    assert result["status"] == "needs_review"
    assert result["issues"] == ["daily_security_working_count_must_be_two_or_three"]


def test_missing_pattern_is_not_guessed():
    result = build_security_day_proposal(
        {"fayez": "OFF", "yasser": "19:00", "walid": "07:00"},
        PATTERNS,
    )
    assert result["status"] == "needs_review"
    assert len(result["issues"]) == 2


def test_duplicate_start_times_require_review():
    result = build_security_day_proposal(
        {"fayez": "08:00", "yasser": "08:00", "walid": "OFF"},
        PATTERNS,
    )
    assert result["status"] == "needs_review"
    assert result["issues"] == ["duplicate_security_shift_start_times"]
