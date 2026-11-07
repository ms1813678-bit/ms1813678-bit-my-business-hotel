from datetime import date, time
from io import BytesIO

from openpyxl import Workbook
import pytest

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
    data = _workbook_bytes([["Date", "Fayez"]])
    try:
        parse_security_roster_xlsx(data, {"Fayez": 11})
    except ValueError as exc:
        assert str(exc) == "aliases_must_resolve_to_at_least_two_unique_security_employees"
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


def test_employee_code_and_fingerprint_are_valid_exact_headers():
    from io import BytesIO
    from openpyxl import Workbook
    from app.security_roster_import import parse_security_roster_xlsx

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Date", "SEC-01", "FP-02", "Walid"])
    sheet.append([date(2026, 11, 1), "00:00", "08:00", "16:00"])
    output = BytesIO()
    workbook.save(output)
    parsed = parse_security_roster_xlsx(
        output.getvalue(),
        {
            "Fayez": 1, "SEC-01": 1,
            "Yasser": 2, "FP-02": 2,
            "Walid": 3,
        },
    )
    assert parsed["status"] == "parsed_for_review"
    assert parsed["employee_headers"] == {1: "SEC-01", 2: "FP-02", 3: "Walid"}
    assert len(parsed["records"]) == 1


def test_two_alias_columns_for_same_employee_are_rejected():
    from io import BytesIO
    from openpyxl import Workbook
    from app.security_roster_import import parse_security_roster_xlsx

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Date", "Fayez", "SEC-01", "Yasser", "Walid"])
    sheet.append([date(2026, 11, 1), "00:00", "08:00", "08:00", "16:00"])
    output = BytesIO()
    workbook.save(output)
    parsed = parse_security_roster_xlsx(
        output.getvalue(),
        {"Fayez": 1, "SEC-01": 1, "Yasser": 2, "Walid": 3},
    )
    assert parsed["status"] == "needs_review"
    assert parsed["records"] == []
    assert parsed["issues"][0]["reason"] == "duplicate_employee_header"


def test_header_only_workbook_is_not_reported_as_ready():
    data = _workbook_bytes([["Date", "Fayez", "Yasser", "Walid"]])
    result = parse_security_roster_xlsx(data, ALIASES)
    assert result["status"] == "needs_review"
    assert result["records"] == []
    assert result["issues"][0]["reason"] == "no_valid_roster_rows"


def test_normalized_alias_collision_is_rejected_before_parsing():
    data = _workbook_bytes([["Date", "Fayez", "Yasser", "Walid"]])
    with pytest.raises(ValueError, match="employee_aliases_contain_normalization_collisions"):
        parse_security_roster_xlsx(
            data,
            {
                " Fayez ": 1,
                "fayez": 2,
                "Yasser": 2,
                "Walid": 3,
            },
        )


def test_auto_detection_skips_header_only_sheet_and_uses_later_roster_sheet():
    workbook = Workbook()
    first = workbook.active
    first.title = "Instructions"
    first.append(["Date", "Fayez", "Yasser", "Walid"])
    second = workbook.create_sheet("November")
    second.append(["Date", "Fayez", "Yasser", "Walid"])
    second.append([date(2026, 11, 1), "OFF", time(20, 0), time(8, 0)])
    output = BytesIO()
    workbook.save(output)

    parsed = parse_security_roster_xlsx(output.getvalue(), ALIASES)

    assert parsed["sheet_name"] == "November"
    assert parsed["status"] == "parsed_for_review"
    assert len(parsed["records"]) == 1
    assert parsed["records"][0]["work_date"] == date(2026, 11, 1)


def test_explicit_header_only_sheet_is_not_silently_replaced():
    workbook = Workbook()
    first = workbook.active
    first.title = "Instructions"
    first.append(["Date", "Fayez", "Yasser", "Walid"])
    second = workbook.create_sheet("November")
    second.append(["Date", "Fayez", "Yasser", "Walid"])
    second.append([date(2026, 11, 1), "OFF", time(20, 0), time(8, 0)])
    output = BytesIO()
    workbook.save(output)

    parsed = parse_security_roster_xlsx(
        output.getvalue(), ALIASES, sheet_name="Instructions"
    )

    assert parsed["sheet_name"] == "Instructions"
    assert parsed["status"] == "needs_review"
    assert parsed["records"] == []
    assert parsed["issues"][0]["reason"] == "no_valid_roster_rows"


@pytest.mark.parametrize(
    "names,aliases",
    [
        (["Fayez", "Yasser"], {"Fayez": 21, "Yasser": 22}),
        (
            ["Fayez", "Yasser", "Walid", "Hany"],
            {"Fayez": 31, "Yasser": 32, "Walid": 33, "Hany": 34},
        ),
    ],
)
def test_roster_parser_supports_two_or_more_employee_columns(names, aliases):
    headers = ["Date", *names]
    values = [date(2026, 11, 1), *([time(0, 0)] * len(names))]
    parsed = parse_security_roster_xlsx(
        _workbook_bytes([headers, values]),
        aliases,
    )
    assert parsed["status"] == "parsed_for_review"
    assert set(parsed["employee_headers"]) == set(aliases.values())
    assert set(parsed["records"][0]["roster"]) == set(aliases.values())
    assert parsed["records"][0]["roster"][next(iter(aliases.values()))] == time(0, 0)


def test_unmapped_employee_header_is_reported_instead_of_ignored():
    data = _workbook_bytes([
        ["Date", "Fayez", "Yasser", "Walid", "Unknown Guard"],
        [date(2026, 11, 1), time(8, 0), time(16, 0), time(0, 0), time(20, 0)],
    ])
    result = parse_security_roster_xlsx(data, ALIASES)

    assert result["status"] == "needs_review"
    assert len(result["records"]) == 1
    assert any(
        issue["reason"] == "unknown_or_unmapped_employee_header"
        and issue["header"] == "Unknown Guard"
        and issue["column"] == 5
        for issue in result["issues"]
    )


def test_roster_accepts_work_date_label_and_ignores_known_metadata_columns():
    data = _workbook_bytes([
        ["Work Date", "Fayez", "Yasser", "Walid", "Notes", "Total Hours"],
        [date(2026, 11, 1), time(8, 0), time(16, 0), time(0, 0), "checked", 24],
    ])
    result = parse_security_roster_xlsx(data, ALIASES)

    assert result["status"] == "parsed_for_review"
    assert len(result["records"]) == 1
    assert result["records"][0]["work_date"] == date(2026, 11, 1)
    assert result["issues"] == []
