from datetime import time

from app.security_roster import build_security_day_proposal


PATTERNS = [
    {"id": 1, "start_time": time(8, 0), "end_time": time(20, 0)},
    {"id": 2, "start_time": time(20, 0), "end_time": time(8, 0)},
    {"id": 3, "start_time": time(0, 0), "end_time": time(8, 0)},
    {"id": 4, "start_time": time(8, 0), "end_time": time(16, 0)},
    {"id": 5, "start_time": time(16, 0), "end_time": time(0, 0)},
    {"id": 6, "start_time": time(0, 0), "end_time": time(12, 0)},
    {"id": 7, "start_time": time(12, 0), "end_time": time(0, 0)},
]


def _ids(result):
    return [(item["employee_id"], item["duration_hours"], item["shift_pattern_id"])
            for item in result["assignments"]]


def test_two_working_security_employees_receive_12_hour_shifts():
    result = build_security_day_proposal(
        {"yasser": "20:00", "walid": "08:00"},
        PATTERNS,
    )
    assert result["status"] == "ready_for_admin_review"
    assert result["issues"] == []
    assert _ids(result) == [("yasser", 12, 2), ("walid", 12, 1)]
    assert result["assignments"][0]["end_time"] == time(8, 0)
    assert result["assignments"][1]["end_time"] == time(20, 0)


def test_two_guards_on_the_same_morning_shift_miss_night_coverage():
    result = build_security_day_proposal(
        {"yasser": "08:00", "walid": "08:00"},
        PATTERNS,
    )
    assert result["status"] == "needs_review"
    assert result["assignments"] == []
    assert result["issues"] == ["security_shifts_must_cover_24_hours_without_gaps_or_overlaps"]


def test_one_working_guard_is_rejected_even_when_the_roster_is_larger():
    result = build_security_day_proposal(
        {"fayez": "OFF", "yasser": "OFF", "walid": "08:00"},
        PATTERNS,
    )
    assert result["status"] == "needs_review"
    assert result["assignments"] == []
    assert result["issues"] == ["daily_security_working_count_must_cover_full_day"]


def test_three_working_security_employees_receive_8_hour_shifts():
    result = build_security_day_proposal(
        {"fayez": "00:00", "yasser": "16:00", "walid": "08:00"},
        PATTERNS,
    )
    assert result["status"] == "ready_for_admin_review"
    assert {item["duration_hours"] for item in result["assignments"]} == {8}
    assert {item["shift_pattern_id"] for item in result["assignments"]} == {3, 4, 5}
    fayez = next(item for item in result["assignments"] if item["employee_id"] == "fayez")
    assert fayez["start_time"] == time(0, 0)
    assert fayez["end_time"] == time(8, 0)


def test_three_guards_missing_a_required_period_are_rejected():
    result = build_security_day_proposal(
        {"one": "00:00", "two": "09:00", "three": "16:00"}, PATTERNS
    )
    assert result["status"] == "needs_review"
    assert result["assignments"] == []
    assert result["issues"] == [
        "security_shifts_must_cover_24_hours_without_gaps_or_overlaps"
    ]


def test_four_guards_two_sharing_morning_are_accepted():
    result = build_security_day_proposal(
        {"a": "08:00", "b": "08:00", "c": "16:00", "d": "00:00"},
        PATTERNS,
    )
    assert result["status"] == "ready_for_admin_review"
    assert len(result["assignments"]) == 4
    assert {item["duration_hours"] for item in result["assignments"]} == {8}
    morning = [item for item in result["assignments"] if item["start_time"] == time(8, 0)]
    assert len(morning) == 2
    assert {item["shift_pattern_id"] for item in morning} == {4}


def test_four_guards_two_sharing_night_are_accepted():
    result = build_security_day_proposal(
        {"a": "08:00", "b": "16:00", "c": "00:00", "d": "00:00"},
        PATTERNS,
    )
    assert result["status"] == "ready_for_admin_review"
    assert len(result["assignments"]) == 4
    assert sum(item["start_time"] == time(0, 0) for item in result["assignments"]) == 2


def test_four_guards_all_on_one_shift_are_rejected():
    result = build_security_day_proposal(
        {"a": "08:00", "b": "08:00", "c": "08:00", "d": "08:00"},
        PATTERNS,
    )
    assert result["status"] == "needs_review"
    assert result["assignments"] == []
    assert result["issues"] == ["security_shifts_must_cover_24_hours_without_gaps_or_overlaps"]


def test_five_guards_sharing_shifts_are_accepted_when_coverage_is_complete():
    result = build_security_day_proposal(
        {"a": "08:00", "b": "08:00", "c": "16:00", "d": "16:00", "e": "00:00"},
        PATTERNS,
    )
    assert result["status"] == "ready_for_admin_review"
    assert len(result["assignments"]) == 5
    assert {item["duration_hours"] for item in result["assignments"]} == {8}


def test_five_person_roster_with_only_two_working_uses_12_hour_model():
    result = build_security_day_proposal(
        {"a": "08:00", "b": "20:00", "c": "OFF", "d": "OFF", "e": "OFF"},
        PATTERNS,
    )
    assert result["status"] == "ready_for_admin_review"
    assert _ids(result) == [("a", 12, 1), ("b", 12, 2)]


def test_five_person_roster_with_three_working_uses_8_hour_model():
    result = build_security_day_proposal(
        {"a": "08:00", "b": "16:00", "c": "00:00", "d": "OFF", "e": "OFF"},
        PATTERNS,
    )
    assert result["status"] == "ready_for_admin_review"
    assert len(result["assignments"]) == 3
    assert {item["duration_hours"] for item in result["assignments"]} == {8}


def test_five_person_roster_with_four_working_uses_8_hour_model():
    result = build_security_day_proposal(
        {"a": "08:00", "b": "08:00", "c": "16:00", "d": "00:00", "e": "OFF"},
        PATTERNS,
    )
    assert result["status"] == "ready_for_admin_review"
    assert len(result["assignments"]) == 4
    assert {item["duration_hours"] for item in result["assignments"]} == {8}


def test_missing_shift_pattern_requires_review_and_rejects_the_whole_day():
    patterns = [
        {"id": 4, "start_time": time(8, 0), "end_time": time(16, 0)},
        {"id": 5, "start_time": time(16, 0), "end_time": time(0, 0)},
    ]
    result = build_security_day_proposal(
        {"yasser": "20:00", "walid": "08:00"},
        patterns,
    )
    assert result["status"] == "needs_review"
    assert result["assignments"] == []
    assert len(result["issues"]) == 2
    assert all(issue.startswith("shift_pattern_resolution_missing:") for issue in result["issues"])


def test_ambiguous_shift_pattern_requires_review():
    patterns = PATTERNS + [
        {"id": 11, "start_time": "08:00", "end_time": "20:00"},
    ]
    result = build_security_day_proposal(
        {"yasser": "20:00", "walid": "08:00"},
        patterns,
    )
    assert result["status"] == "needs_review"
    assert result["assignments"] == []
    assert any(issue.startswith("shift_pattern_resolution_ambiguous:walid:") for issue in result["issues"])
    assert all("yasser" not in issue for issue in result["issues"])


def test_unexpected_start_times_are_rejected():
    result = build_security_day_proposal(
        {"yasser": "19:00", "walid": "07:00"},
        PATTERNS,
    )
    assert result["status"] == "needs_review"
    assert result["assignments"] == []
    assert result["issues"] == ["security_shifts_must_cover_24_hours_without_gaps_or_overlaps"]


def test_invalid_start_text_requires_review():
    result = build_security_day_proposal(
        {"yasser": "morning", "walid": "08:00"},
        PATTERNS,
    )
    assert result["status"] == "needs_review"
    assert result["assignments"] == []
    assert result["issues"] == ["invalid_shift_start_for_employee:yasser"]


def test_overnight_12_hour_shift_keeps_the_start_clock_and_does_not_change_date():
    result = build_security_day_proposal(
        {"yasser": time(20, 0), "walid": time(8, 0)},
        PATTERNS,
    )
    assert result["status"] == "ready_for_admin_review"
    yasser = result["assignments"][0]
    assert yasser["employee_id"] == "yasser"
    assert yasser["start_time"] == time(20, 0)
    assert yasser["end_time"] == time(8, 0)
    assert yasser["duration_hours"] == 12
    assert "work_date" not in yasser
    assert "work_date" not in result


def test_old_midnight_noon_pair_is_not_an_approved_12_hour_model():
    result = build_security_day_proposal(
        {"fayez": "00:00", "walid": "12:00"},
        PATTERNS,
    )
    assert result["status"] == "needs_review"
    assert result["assignments"] == []


def test_repeated_start_with_an_off_colleague_is_not_rejected_as_a_duplicate_label():
    result = build_security_day_proposal(
        {"fayez": "08:00", "yasser": "08:00", "walid": "OFF"},
        PATTERNS,
    )
    assert result["status"] == "needs_review"
    assert result["issues"] != ["duplicate_security_shift_start_times"]
    assert result["issues"] == ["security_shifts_must_cover_24_hours_without_gaps_or_overlaps"]


def test_four_person_roster_with_three_working_uses_eight_hour_coverage():
    roster = {"one": "00:00", "two": "08:00", "three": "16:00", "four": "OFF"}
    result = build_security_day_proposal(roster, PATTERNS)
    assert result["status"] == "ready_for_admin_review"
    assert len(result["assignments"]) == 3
    assert {item["duration_hours"] for item in result["assignments"]} == {8}


def test_team_with_one_working_person_is_rejected():
    result = build_security_day_proposal(
        {"one": "OFF", "two": "OFF", "three": "08:00"}, PATTERNS
    )
    assert result["status"] == "needs_review"
    assert result["issues"] == ["daily_security_working_count_must_cover_full_day"]


def test_all_active_security_employees_cannot_be_off_on_the_same_day():
    result = build_security_day_proposal(
        {"one": "OFF", "two": "OFF", "three": "OFF"}, PATTERNS
    )
    assert result["status"] == "needs_review"
    assert result["assignments"] == []
