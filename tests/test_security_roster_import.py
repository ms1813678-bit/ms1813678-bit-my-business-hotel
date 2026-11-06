from datetime import date, time
from io import BytesIO

from openpyxl import Workbook

from app.security_roster_import import parse_security_roster_xlsx


def _workbook_bytes(rows):
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    output = BytesIO()
    wb.save(output)
    return output.getvalue()


ALIASES = {"Fayez": 11, "Yasser": 12, "Walid": 13}


def test_parse_roster_distinguishes_off_from_midnight():
    data = _workbook_bytes([
        ["Date", "Fayez", "Yasser", "Walid"],
        [date(2026, 11, 1), "OFF", time(20, 0), time(8, 0)],
        [date(2026, 11, 2), time(0, 0), time(8, 0), time(16, 0)],
    ])
    result = parse_security_roster_xlsx(data, ALIASES)
    assert result["status"] == "parsed_for_review"
    assert result["records"][0]["roster"] == {
        11: "OFF", 12: time(20, 0), 13: time(8, 0)
    }
    assert result["records"][1]["roster"][11] == time(0, 0)


def test_invalid_cell_is_reported_not_guessed():
    data = _workbook_bytes([
        ["Date", "Fayez", "Yasser", "Walid"],
        [date(2026, 11, 1), "OFF", "unknown", time(8, 0)],
    ])
    result = parse_security_roster_xlsx(data, ALIASES)
    assert result["status"] == "needs_review"
    assert result["issues"][0]["reason"] == "missing_or_invalid_shift_value"


def test_duplicate_dates_are_reported():
    data = _workbook_bytes([
        ["Date", "Fayez", "Yasser", "Walid"],
        [date(2026, 11, 1), "OFF", time(20, 0), time(8, 0)],
        [date(2026, 11, 1), "OFF", time(20, 0), time(8, 0)],
    ])
    result = parse_security_roster_xlsx(data, ALIASES)
    assert result["status"] == "needs_review"
    assert any(x["reason"] == "duplicate_date" for x in result["issues"])


def test_unknown_employee_mapping_is_rejected():
    data = _workbook_bytes([["Date", "Fayez", "Yasser", "Walid"]])
    try:
        parse_security_roster_xlsx(data, {"Fayez": 11, "Yasser": 12})
    except ValueError as exc:
        assert str(exc) == "exactly_three_unique_security_employee_aliases_required"
    else:
        raise AssertionError("expected ValueError")


def test_duplicate_employee_headers_are_reported_without_silently_collapsing():
    data = _workbook_bytes([
        ["Date", "Fayez", "Fayez", "Yasser", "Walid"],
        [date(2026, 11, 1), "OFF", time(8, 0), time(20, 0), time(8, 0)],
    ])
    result = parse_security_roster_xlsx(data, ALIASES)
    assert result["status"] == "needs_review"
    assert result["records"] == []
    assert any(
        issue["reason"] == "duplicate_employee_header"
        and issue["row"] == 1
        and issue["column"] == 3
        for issue in result["issues"]
    )
