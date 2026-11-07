"""Parse a monthly Security roster for the fixed three-person team."""
from datetime import date, datetime, time
from io import BytesIO
import re
import unicodedata

from openpyxl import load_workbook


def _norm(value):
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"\s+", " ", text)


def _cell_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        raw = value.strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m", "%d-%m"):
            try:
                parsed = datetime.strptime(raw, fmt)
                if fmt in ("%d/%m", "%d-%m"):
                    raise ValueError("date_without_year")
                return parsed.date()
            except ValueError:
                continue
    return None


def _cell_shift(value):
    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0)
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    if isinstance(value, (int, float)) and 0 <= value < 1:
        minutes = round(value * 24 * 60) % (24 * 60)
        return time(minutes // 60, minutes % 60)
    if isinstance(value, str):
        raw = value.strip()
        if raw.casefold() == "off":
            return "OFF"
        try:
            return time.fromisoformat(raw).replace(second=0, microsecond=0)
        except ValueError:
            return None
    return None


def parse_security_roster_xlsx(file_bytes, employee_aliases, sheet_name=None):
    """Return normalized daily roster rows; never writes or approves assignments.

    employee_aliases maps known spreadsheet employee names to stable employee IDs.
    The sheet must contain one date/day column and one exact header for every
    configured employee (exactly three). Dates without a year are rejected.
    """
    if not isinstance(file_bytes, (bytes, bytearray)) or not file_bytes:
        raise ValueError("empty_excel_file")
    aliases = {_norm(name): employee_id for name, employee_id in employee_aliases.items()}
    if len(aliases) != len(employee_aliases):
        raise ValueError("employee_aliases_contain_normalization_collisions")
    expected_employee_ids = set(aliases.values())
    if len(expected_employee_ids) != 3:
        raise ValueError("aliases_must_resolve_to_exactly_three_unique_security_employees")

    workbook = None
    try:
        workbook = load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
        selected_sheets = [workbook[sheet_name]] if sheet_name else workbook.worksheets
        # Read all worksheet XML while the file error boundary is active.
        # openpyxl read_only worksheets are lazy and can fail during iteration.
        sheets = [
            (sheet.title, list(sheet.iter_rows(values_only=True)))
            for sheet in selected_sheets
        ]
    except Exception as exc:
        raise ValueError("invalid_or_unsupported_excel_workbook") from exc
    finally:
        if workbook is not None:
            workbook.close()

    empty_header_result = None
    for sheet_title, rows in sheets:
        header = None
        date_col = None
        employee_cols = {}
        employee_headers = {}
        header_row = None
        for row_idx, row in enumerate(rows, start=1):
            normalized = [_norm(value) for value in row]
            employee_positions = [
                (index, alias)
                for index, alias in enumerate(normalized)
                if alias in aliases
            ]
            date_candidates = [
                index
                for index, value in enumerate(normalized)
                if value in {
                    "date", "work date", "day", "التاريخ", "تاريخ العمل", "اليوم"
                }
            ]
            if len(date_candidates) != 1:
                continue

            positions_by_employee = {}
            for index, alias in employee_positions:
                positions_by_employee.setdefault(aliases[alias], []).append(index)
            duplicate_headers = [
                {
                    "row": row_idx,
                    "column": index + 1,
                    "employee_header": str(row[index]).strip(),
                    "reason": "duplicate_employee_header",
                }
                for indexes in positions_by_employee.values()
                if len(indexes) > 1
                for index in indexes[1:]
            ]
            if duplicate_headers:
                return {
                    "sheet_name": sheet_title,
                    "employee_headers": {},
                    "records": [],
                    "issues": duplicate_headers,
                    "status": "needs_review",
                }

            found = {
                employee_id: indexes[0]
                for employee_id, indexes in positions_by_employee.items()
            }
            if set(found) == expected_employee_ids:
                header = row
                employee_cols = found
                employee_headers = {
                    employee_id: str(row[index]).strip()
                    for employee_id, index in found.items()
                }
                date_col = date_candidates[0]
                header_row = row_idx
                break

        if header is None:
            continue

        known_header_columns = {date_col, *employee_cols.values()}
        metadata_headers = {
            "notes", "note", "remarks", "remark", "comments", "comment",
            "shift", "shift type", "total", "total hours", "working hours",
            "department", "employee count", "ملاحظات", "ملاحظة", "الوردية",
            "نوع الوردية", "إجمالي الساعات", "اجمالي الساعات", "القسم",
        }
        unknown_headers = [
            {
                "row": header_row,
                "column": index + 1,
                "header": str(value).strip(),
                "reason": "unknown_or_unmapped_employee_header",
            }
            for index, value in enumerate(header)
            if value is not None
            and str(value).strip()
            and index not in known_header_columns
            and _norm(value) not in metadata_headers
        ]
        records = []
        issues = list(unknown_headers)
        seen_dates = set()
        for row_idx, row in enumerate(rows[header_row:], start=header_row + 1):
            if not row or date_col >= len(row):
                continue
            work_date = _cell_date(row[date_col])
            if work_date is None:
                if all(value is None or str(value).strip() == "" for value in row):
                    continue
                issues.append({
                    "row": row_idx,
                    "reason": "invalid_or_missing_date",
                })
                continue
            if work_date in seen_dates:
                issues.append({
                    "row": row_idx,
                    "date": work_date.isoformat(),
                    "reason": "duplicate_date",
                })
                continue
            seen_dates.add(work_date)

            roster = {}
            for employee_id, column_index in employee_cols.items():
                value = row[column_index] if column_index < len(row) else None
                shift = _cell_shift(value)
                if shift is None:
                    issues.append({
                        "row": row_idx,
                        "date": work_date.isoformat(),
                        "employee_id": employee_id,
                        "reason": "missing_or_invalid_shift_value",
                    })
                else:
                    roster[employee_id] = shift
            if len(roster) == len(employee_cols):
                records.append({"work_date": work_date, "roster": roster})

        if not records and not issues:
            empty_header_result = {
                "sheet_name": sheet_title,
                "employee_headers": employee_headers,
                "records": [],
                "issues": [{
                    "row": header_row,
                    "reason": "no_valid_roster_rows",
                }],
                "status": "needs_review",
            }
            # A header-only worksheet is not necessarily the roster sheet.
            # When auto-detecting, keep looking for a later worksheet with
            # actual rows; an explicitly selected worksheet remains final.
            if sheet_name:
                return empty_header_result
            continue

        return {
            "sheet_name": sheet_title,
            "employee_headers": employee_headers,
            "records": records,
            "issues": issues,
            "status": "needs_review" if issues else "parsed_for_review",
        }

    if empty_header_result is not None:
        return empty_header_result
    raise ValueError("security_roster_header_not_found")
