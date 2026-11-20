from datetime import date, datetime, time

from app.security_roster_compare import (
    build_security_fingerprint_comparison_report,
    compare_one_assignment,
    detect_off_day_punches,
    format_security_comparison_report_ar,
    is_security_comparison_request,
    parse_work_date_from_question,
)


def test_parse_work_date_from_question():
    assert parse_work_date_from_question("قارن الأمن يوم 2026-11-02") == date(2026, 11, 2)


def test_is_security_comparison_request_detects_arabic():
    assert is_security_comparison_request("قارن جدول الأمن مع البصمة ليوم 2026-11-02")
    assert not is_security_comparison_request("كم عدد الموظفين النشطين؟")


def test_matched_shift_has_no_mismatches():
    result = compare_one_assignment(
        employee_id=1,
        employee_code="MY-0001",
        employee_name="حارس 1",
        work_date=date(2026, 11, 2),
        shift_start=time(8, 0),
        shift_end=time(20, 0),
        shift_pattern_id=1,
        fingerprint_punches=[
            datetime(2026, 11, 2, 8, 0),
            datetime(2026, 11, 2, 20, 0),
        ],
    )
    assert result["status"] == "matched"
    assert result["mismatches"] == []


def test_absence_and_late_and_early_are_reported():
    absent = compare_one_assignment(
        employee_id=1,
        employee_code="MY-0001",
        employee_name="حارس 1",
        work_date=date(2026, 11, 2),
        shift_start=time(8, 0),
        shift_end=time(20, 0),
        shift_pattern_id=1,
        fingerprint_punches=[],
    )
    assert absent["status"] == "needs_review"
    assert absent["mismatches"][0]["mismatch_type"] == "absence_despite_assigned_shift"

    late = compare_one_assignment(
        employee_id=1,
        employee_code="MY-0001",
        employee_name="حارس 1",
        work_date=date(2026, 11, 2),
        shift_start=time(8, 0),
        shift_end=time(20, 0),
        shift_pattern_id=1,
        fingerprint_punches=[
            datetime(2026, 11, 2, 8, 25),
            datetime(2026, 11, 2, 20, 0),
        ],
    )
    types = {item["mismatch_type"] for item in late["mismatches"]}
    assert "late_arrival" in types
    assert late["mismatches"][0]["difference_minutes"] == 25

    early = compare_one_assignment(
        employee_id=1,
        employee_code="MY-0001",
        employee_name="حارس 1",
        work_date=date(2026, 11, 2),
        shift_start=time(8, 0),
        shift_end=time(20, 0),
        shift_pattern_id=1,
        fingerprint_punches=[
            datetime(2026, 11, 2, 8, 0),
            datetime(2026, 11, 2, 19, 30),
        ],
    )
    types = {item["mismatch_type"] for item in early["mismatches"]}
    assert "early_departure" in types


def test_overnight_shift_uses_starting_work_date_window():
    result = compare_one_assignment(
        employee_id=2,
        employee_code="MY-0002",
        employee_name="حارس 2",
        work_date=date(2026, 11, 2),
        shift_start=time(20, 0),
        shift_end=time(8, 0),
        shift_pattern_id=2,
        fingerprint_punches=[
            datetime(2026, 11, 2, 20, 0),
            datetime(2026, 11, 3, 8, 0),
        ],
    )
    assert result["status"] == "matched"


def test_off_day_punch_is_flagged():
    mismatches = detect_off_day_punches(
        employee_id=3,
        employee_code="MY-0003",
        employee_name="حارس 3",
        work_date=date(2026, 11, 2),
        fingerprint_punches=[datetime(2026, 11, 2, 10, 0)],
        assigned_employee_ids={1, 2},
    )
    assert len(mismatches) == 1
    assert mismatches[0]["mismatch_type"] == "punch_on_off_day"


def test_day_report_and_arabic_formatting():
    report = build_security_fingerprint_comparison_report(
        work_date=date(2026, 11, 2),
        assignments=[{
            "employee_id": 1,
            "employee_code": "MY-0001",
            "employee_name": "حارس 1",
            "start_time": time(8, 0),
            "end_time": time(20, 0),
            "shift_pattern_id": 1,
        }],
        security_employees=[
            {"id": 1, "code": "MY-0001", "full_name": "حارس 1"},
            {"id": 2, "code": "MY-0002", "full_name": "حارس 2"},
        ],
        fingerprint_punches_by_employee={
            1: [],
            2: [datetime(2026, 11, 2, 11, 0)],
        },
    )
    assert report["status"] == "needs_review"
    assert report["mismatch_count"] >= 2
    text = format_security_comparison_report_ar(report)
    assert "تقرير مقارنة جدول الأمن مع البصمة" in text
    assert "لم يتم اعتماد أي حضور" in text
    assert "MY-0001" in text
