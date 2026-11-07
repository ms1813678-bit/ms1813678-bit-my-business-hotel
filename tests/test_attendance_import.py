from datetime import date, datetime, time
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

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

    assert len(parsed["events"]) == 1
    assert parsed["events"][0]["employee_id"] is None
    assert parsed["events"][0]["raw_employee_code"] == "FP-UNKNOWN"
    assert parsed["events"][0]["punch_at"] == datetime(2026, 11, 1, 7, 50)
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


def test_corrupt_xlsx_is_reported_as_a_reviewable_import_error():
    with pytest.raises(ValueError, match="invalid_or_unsupported_excel_workbook"):
        parse_attendance_workbook(
            b"this is not an xlsx workbook",
            "corrupt.xlsx",
            "fingerprint",
            ALIASES,
        )


def test_xlsx_with_lazy_corrupt_worksheet_xml_is_reported_as_reviewable_error():
    valid = workbook_bytes([("Sheet1", [
        ["AC-No.", "Name", "Date", "Clock In", "Clock Out"],
        ["SEC-01", "Fayez", date(2026, 11, 1), time(8, 0), time(16, 0)],
    ])])
    corrupted = BytesIO()
    with ZipFile(BytesIO(valid), "r") as source, ZipFile(
        corrupted, "w", ZIP_DEFLATED
    ) as target:
        for item in source.infolist():
            payload = (
                b"<worksheet><sheetData><row>"
                if item.filename == "xl/worksheets/sheet1.xml"
                else source.read(item.filename)
            )
            target.writestr(item, payload)

    with pytest.raises(ValueError, match="invalid_or_unsupported_excel_workbook"):
        parse_attendance_workbook(
            corrupted.getvalue(), "lazy-corrupt.xlsx", "program_report", ALIASES
        )



def test_auto_detection_skips_header_only_sheet_and_reads_later_data_sheet():
    raw = workbook_bytes([
        ("Instructions", [
            ["AC-No.", "Name", "Date", "Clock In", "Clock Out"],
        ]),
        ("November", [
            ["AC-No.", "Name", "Date", "Clock In", "Clock Out"],
            ["SEC-01", "Fayez", date(2026, 11, 1), time(8, 2), time(16, 5)],
        ]),
    ])
    parsed = parse_attendance_workbook(
        raw, "program-multisheet.xlsx", "program_report", ALIASES
    )

    assert parsed["sheet_count"] == 2
    assert len(parsed["events"]) == 2
    assert {item["source_sheet"] for item in parsed["events"]} == {"November"}
    assert parsed["period_start"] == date(2026, 11, 1)
    assert parsed["period_end"] == date(2026, 11, 1)
    assert parsed["issues"] == []


def test_fingerprint_att_log_matrix_parses_day_columns_and_concatenated_tokens():
    days = list(range(1, 32))
    rows = [
        ["Attendance Record Report"],
        ["Stat.Date:", "2026-10-01 ~ 2026-10-31"],
        ["Att. Time", None, "2026-10-01 ~ 2026-10-31"],
        days,
        ["ID:", None, 1.0, None, None, None, None, None, "Name:", None, "Employee One"],
        ["00:2507:5016:09", "15:48 23:54"] + [None] * 29,
        ["ID:", None, "5", None, None, None, None, None, "Name:", None, "Employee Two"],
        ["08:0208:0216:5816:58"] + [None] * 30,
        ["ID:", None, "999", None, None, None, None, None, "Name:", None, "Unknown Employee"],
        ["08:00"] + [None] * 30,
    ]
    report_range = "2026-10-01 ~ 2026-10-31"
    raw = workbook_bytes([
        ("Schedule Infor.", [
            ["Schedule Information Report"], ["Stat.Date:", report_range],
            ["ID", "Name", "Department"] + days,
        ]),
        ("Att. Stat.", [
            ["Statistical Report of Attendance"], ["Stat.Date:", report_range],
            ["ID", "Name", "Department"],
        ]),
        ("Att.log report", rows),
        ("Exception Stat.", [
            ["Exception Statistic Report"], ["Stat.Date:", report_range],
            ["ID", "Name", "Department", "Date"],
        ]),
    ])
    parsed = parse_attendance_workbook(
        raw, "1_StandardReport.xlsx", "fingerprint",
        {"1": 101, "Employee One": 101, "5": 105, "Employee Two": 105},
    )

    assert parsed["sheet_count"] == 4
    assert parsed["period_start"] == date(2026, 10, 1)
    assert parsed["period_end"] == date(2026, 10, 31)
    assert len(parsed["events"]) == 10
    assert [event["punch_at"].time() for event in parsed["events"][:5]] == [
        time(0, 25), time(7, 50), time(16, 9), time(15, 48), time(23, 54)
    ]
    assert [event["punch_at"] for event in parsed["events"][3:5]] == [
        datetime(2026, 10, 2, 15, 48), datetime(2026, 10, 2, 23, 54)
    ]
    assert [event["punch_at"].time() for event in parsed["events"][5:9]] == [
        time(8, 2), time(8, 2), time(16, 58), time(16, 58)
    ]
    assert [event["punch_at"].time() for event in parsed["events"][9:]] == [time(8, 0)]
    assert [event["employee_id"] for event in parsed["events"]] == [
        101, 101, 101, 101, 101, 105, 105, 105, 105, None
    ]
    assert parsed["events"][0]["source_sheet"] == "Att.log report"
    assert parsed["events"][0]["source_row"] == 6
    assert parsed["events"][0]["raw_timestamp"] == "00:2507:5016:09"
    assert [issue["reason"] for issue in parsed["issues"]] == ["unknown_employee"]
