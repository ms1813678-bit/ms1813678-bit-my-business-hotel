from datetime import date, datetime, time
from io import BytesIO

from openpyxl import Workbook
import pytest

from app.attendance_import import parse_attendance_workbook


ALIASES = {
    "SEC-01": 1,
    "Fayez": 1,
    "FP-02": 2,
    "Yasser": 2,
    "FP-03": 3,
    "Walid": 3,
}


def workbook_bytes(sheets):
    workbook = Workbook()
    first = workbook.active
    first.title = sheets[0][0]
    for row in sheets[0][1]:
        first.append(row)
    for title, rows in sheets[1:]:
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_program_report_parses_exact_employee_code_and_in_out_without_mutating_source():
    raw = workbook_bytes([("Sheet1", [
        ["AC-No.", "Name", "Date", "Clock In", "Clock Out"],
        ["SEC-01", "Fayez", date(2026, 11, 1), time(8, 2), time(16, 5)],
    ])])
    parsed = parse_attendance_workbook(raw, "1-11-2026.xlsx", "program_report", ALIASES)

    assert parsed["period_start"] == date(2026, 11, 1)
    assert parsed["period_end"] == date(2026, 11, 1)
    assert len(parsed["events"]) == 2
    assert parsed["events"][0]["employee_id"] == 1
    assert parsed["events"][0]["event_type"] == "IN"
    assert parsed["events"][0]["punch_at"] == datetime(2026, 11, 1, 8, 2)
    assert parsed["events"][1]["event_type"] == "OUT"
    assert parsed["issues"] == []


def test_fingerprint_report_preserves_all_times_in_one_cell_and_all_sheets():
    raw = workbook_bytes([
        ("Schedule Infor.", [["instructions"]]),
        ("Att.log report", [
            ["AC-No.", "Name", "Date", "Check Time"],
            ["FP-02", "Yasser", date(2026, 11, 1), "00:25 07:50 16:09"],
        ]),
    ])
    parsed = parse_attendance_workbook(raw, "1_StandardReport.xlsx", "fingerprint", ALIASES)

    assert parsed["sheet_count"] == 2
    assert len(parsed["events"]) == 3
    assert [item["punch_at"].time() for item in parsed["events"]] == [
        time(0, 25), time(7, 50), time(16, 9)
    ]
    assert {item["source_sheet"] for item in parsed["events"]} == {"Att.log report"}
    assert {item["source_row"] for item in parsed["events"]} == {2}


def test_unknown_employee_is_reported_and_never_fuzzy_matched():
    raw = workbook_bytes([("Att.log report", [
        ["AC-No.", "Name", "Date", "Check Time"],
        ["FP-UNKNOWN", "Yasser Similar", date(2026, 11, 1), "07:50"],
    ])])
    parsed = parse_attendance_workbook(raw, "fingerprint.xlsx", "fingerprint", ALIASES)

    assert parsed["events"] == []
    assert parsed["issues"][0]["reason"] == "unknown_employee"
    assert parsed["period_start"] == date(2026, 11, 1)
    assert parsed["period_end"] == date(2026, 11, 1)


def test_invalid_time_is_reported_instead_of_fabricated():
    raw = workbook_bytes([("Sheet1", [
        ["AC-No.", "Name", "Date", "Clock In", "Clock Out"],
        ["SEC-01", "Fayez", date(2026, 11, 1), "not-a-time", time(16, 0)],
    ])])
    parsed = parse_attendance_workbook(raw, "program.xlsx", "program_report", ALIASES)

    assert len(parsed["events"]) == 1
    assert parsed["events"][0]["event_type"] == "OUT"
    assert parsed["issues"][0]["reason"] == "invalid_time_value"


def test_rejects_unknown_extension_and_empty_file():
    with pytest.raises(ValueError, match="empty_attendance_file"):
        parse_attendance_workbook(b"", "x.xlsx", "fingerprint", ALIASES)
    with pytest.raises(ValueError, match="unsupported_attendance_file_extension"):
        parse_attendance_workbook(b"abc", "x.csv", "fingerprint", ALIASES)
