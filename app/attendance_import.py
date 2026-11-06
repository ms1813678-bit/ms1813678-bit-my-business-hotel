"""Read attendance exports from the hotel attendance program and fingerprint terminal.

This module parses evidence only. It never writes to the database, changes source cells,
or infers employee identities by fuzzy matching.
"""
from __future__ import annotations

from datetime import date, datetime, time
from io import BytesIO
from pathlib import Path
import re
import unicodedata


DATE_HEADERS = {"date", "work date", "日期", "التاريخ", "اليوم"}
CODE_HEADERS = {"ac-no", "ac no", "employee id", "employee code", "id", "رقم الموظف", "كود الموظف"}
NAME_HEADERS = {"name", "employee name", "الاسم", "اسم الموظف"}
IN_HEADERS = {"clock in", "check in", "in time", "وقت الحضور", "دخول"}
OUT_HEADERS = {"clock out", "check out", "out time", "وقت الانصراف", "خروج"}
EVENT_HEADERS = {"check time", "punch time", "attendance time", "att. time", "time", "وقت البصمة", "وقت"}
EVENT_TYPE_HEADERS = {"state", "status", "event type", "type", "الحالة", "نوع الحركة"}
TIME_TOKEN = re.compile(r"(?<!\\d)([01]?\\d|2[0-3]):([0-5]\\d)(?::([0-5]\\d))?(?!\\d)")


def normalize(value) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"\\s+", " ", text)


def _date_value(value, datemode=0):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and value > 0:
        try:
            import xlrd
            return xlrd.xldate_as_datetime(value, datemode).date()
        except Exception:
            return None
    if isinstance(value, str):
        raw = value.strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
    return None


def _time_tokens(value):
    if isinstance(value, datetime):
        return [value.time().replace(microsecond=0)]
    if isinstance(value, time):
        return [value.replace(microsecond=0)]
    if isinstance(value, (int, float)) and 0 <= value < 1:
        minutes = round(value * 1440) % 1440
        return [time(minutes // 60, minutes % 60)]
    if value is None or str(value).strip() == "":
        return []
    raw = str(value).strip()
    matches = list(TIME_TOKEN.finditer(raw))
    parsed = [
        time(int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))
        for match in matches
    ]
    if parsed:
        return parsed
    try:
        return [time.fromisoformat(raw)]
    except ValueError:
        return []


def _load_rows(file_bytes: bytes, filename: str):
    suffix = Path(filename or "").suffix.casefold()
    if suffix == ".xlsx":
        from openpyxl import load_workbook
        workbook = load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
        try:
            return [
                (sheet.title, list(sheet.iter_rows(values_only=True)), 0)
                for sheet in workbook.worksheets
            ]
        finally:
            workbook.close()
    if suffix == ".xls":
        try:
            import xlrd
        except ImportError as exc:
            raise ValueError("legacy_xls_support_not_installed") from exc
        workbook = xlrd.open_workbook(file_contents=file_bytes, on_demand=True)
        try:
            return [
                (sheet.name, [sheet.row_values(i) for i in range(sheet.nrows)], workbook.datemode)
                for sheet in workbook.sheets()
            ]
        finally:
            workbook.release_resources()
    raise ValueError("unsupported_attendance_file_extension")


def parse_attendance_workbook(file_bytes: bytes, filename: str, source_type: str, employee_aliases: dict):
    """Return raw-positioned punch events and issues for HR review.

    employee_aliases maps exact employee codes, fingerprint IDs, and names to employee IDs.
    Program reports contribute separate in/out events; fingerprint exports preserve every
    time token in event cells (including multiple events in a single cell).
    """
    if not isinstance(file_bytes, (bytes, bytearray)) or not file_bytes:
        raise ValueError("empty_attendance_file")
    if source_type not in {"program_report", "fingerprint"}:
        raise ValueError("unsupported_attendance_source_type")
    aliases = {}
    for alias, employee_id in employee_aliases.items():
        key = normalize(alias)
        if not key or key in aliases and aliases[key] != employee_id:
            raise ValueError("employee_alias_collision")
        aliases[key] = employee_id
    if not aliases:
        raise ValueError("employee_aliases_required")

    sheets = _load_rows(bytes(file_bytes), filename)
    events, issues, dates = [], [], []
    for sheet_name, rows, datemode in sheets:
        header_index = None
        columns = {}
        for row_index, row in enumerate(rows):
            normalized = [normalize(cell) for cell in row]
            found = {
                "date": [i for i, cell in enumerate(normalized) if cell in DATE_HEADERS],
                "code": [i for i, cell in enumerate(normalized) if cell in CODE_HEADERS],
                "name": [i for i, cell in enumerate(normalized) if cell in NAME_HEADERS],
                "in": [i for i, cell in enumerate(normalized) if cell in IN_HEADERS],
                "out": [i for i, cell in enumerate(normalized) if cell in OUT_HEADERS],
                "event": [i for i, cell in enumerate(normalized) if cell in EVENT_HEADERS],
                "type": [i for i, cell in enumerate(normalized) if cell in EVENT_TYPE_HEADERS],
            }
            if len(found["date"]) == 1 and (found["code"] or found["name"]) and (
                source_type == "program_report" and found["in"] and found["out"]
                or source_type == "fingerprint" and (found["event"] or found["in"] or found["out"])
            ):
                header_index = row_index
                columns = {key: value[0] for key, value in found.items() if value}
                break
        if header_index is None:
            continue

        for row_index in range(header_index + 1, len(rows)):
            row = rows[row_index]
            if not any(value is not None and str(value).strip() for value in row):
                continue
            def cell(key):
                index = columns.get(key)
                return row[index] if index is not None and index < len(row) else None

            employee_key = normalize(cell("code")) if cell("code") not in (None, "") else normalize(cell("name"))
            employee_id = aliases.get(employee_key)
            raw_date = cell("date")
            work_date = _date_value(raw_date, datemode)
            if employee_id is None:
                issues.append({"sheet": sheet_name, "row": row_index + 1, "reason": "unknown_employee", "value": str(employee_key)})
                continue
            if work_date is None:
                issues.append({"sheet": sheet_name, "row": row_index + 1, "reason": "invalid_or_missing_date", "value": str(raw_date)})
                continue
            dates.append(work_date)

            values = []
            if source_type == "program_report":
                values = [("IN", cell("in")), ("OUT", cell("out"))]
            else:
                event_value = cell("event")
                if event_value not in (None, ""):
                    values = [("PUNCH", event_value)]
                else:
                    values = [("IN", cell("in")), ("OUT", cell("out"))]

            event_index = 0
            for event_type, raw_value in values:
                if raw_value in (None, ""):
                    continue
                tokens = _time_tokens(raw_value)
                if not tokens:
                    issues.append({"sheet": sheet_name, "row": row_index + 1, "reason": "invalid_time_value", "value": str(raw_value)})
                    continue
                for punch_time in tokens:
                    punch_at = datetime.combine(work_date, punch_time)
                    events.append({
                        "employee_id": employee_id,
                        "raw_employee_code": str(cell("code")) if cell("code") not in (None, "") else None,
                        "raw_employee_name": str(cell("name")) if cell("name") not in (None, "") else None,
                        "punch_at": punch_at,
                        "raw_timestamp": str(raw_value),
                        "event_type": event_type,
                        "source_sheet": sheet_name,
                        "source_row": row_index + 1,
                        "event_index": event_index,
                    })
                    event_index += 1
    if not events and not issues:
        raise ValueError("attendance_headers_not_found")
    return {
        "source_type": source_type,
        "events": events,
        "issues": issues,
        "period_start": min(dates) if dates else None,
        "period_end": max(dates) if dates else None,
        "sheet_count": len(sheets),
    }
