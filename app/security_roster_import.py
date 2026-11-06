"""Parse the monthly three-person Security roster from an Excel workbook."""
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
    The sheet must contain a header row with one date/day column and exactly the
    three configured employee names. Dates without a year are rejected.
    """
    if not isinstance(file_bytes, (bytes, bytearray)) or not file_bytes:
        raise ValueError("empty_excel_file")
    aliases = {_norm(name): employee_id for name, employee_id in employee_aliases.items()}
    if len(aliases) != 3 or len(set(aliases.values())) != 3:
        raise ValueError("exactly_three_unique_security_employee_aliases_required")

    try:
        workbook = load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError("invalid_or_unsupported_excel_workbook") from exc

    try:
        sheets = [workbook[sheet_name]] if sheet_name else workbook.worksheets
        for sheet in sheets:
            header = None
            date_col = None
            employee_cols = {}
            employee_headers = {}
            header_row = None
            for row_idx, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                normalized = [_norm(v) for v in row]
                found = {name: idx for idx, name in enumerate(normalized) if name in aliases}
                date_candidates = [idx for idx, value in enumerate(normalized)
                                   if value in {"date", "day", "التاريخ", "اليوم"}]
                if len(found) == 3 and len(date_candidates) == 1:
                    header = row
                    employee_cols = {aliases[name]: idx for name, idx in found.items()}
                    employee_headers = {aliases[name]: str(row[idx]).strip() for name, idx in found.items()}
                    date_col = date_candidates[0]
                    header_row = row_idx
                    break
            if header is None:
                continue

            records = []
            issues = []
            seen_dates = set()
            for row_idx, row in enumerate(
                sheet.iter_rows(min_row=header_row + 1, values_only=True),
                start=header_row + 1,
            ):
                if not row or date_col >= len(row):
                    continue
                work_date = _cell_date(row[date_col])
                if work_date is None:
                    if all(v is None or str(v).strip() == "" for v in row):
                        continue
                    issues.append({"row": row_idx, "reason": "invalid_or_missing_date"})
                    continue
                if work_date in seen_dates:
                    issues.append({"row": row_idx, "date": work_date.isoformat(),
                                   "reason": "duplicate_date"})
                    continue
                seen_dates.add(work_date)
                roster = {}
                for employee_id, col_idx in employee_cols.items():
                    value = row[col_idx] if col_idx < len(row) else None
                    shift = _cell_shift(value)
                    if shift is None:
                        issues.append({"row": row_idx, "date": work_date.isoformat(),
                                       "employee_id": employee_id,
                                       "reason": "missing_or_invalid_shift_value"})
                    else:
                        roster[employee_id] = shift
                if len(roster) == 3:
                    records.append({"work_date": work_date, "roster": roster})
            return {
                "sheet_name": sheet.title,
                "employee_headers": employee_headers,
                "records": records,
                "issues": issues,
                "status": "needs_review" if issues else "parsed_for_review",
            }
        raise ValueError("security_roster_header_not_found")
    finally:
        workbook.close()
