"""Generate the previous month's HR reports locally on the first day of each month."""
import argparse
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.db import SessionLocal
from app.reports import build_hr_pdf, build_hr_workbook


CAIRO = ZoneInfo("Africa/Cairo")
REPORT_DIR = Path("var") / "reports"


def previous_month_period(today: date) -> tuple[date, date]:
    first_of_current = date(today.year, today.month, 1)
    end = date.fromordinal(first_of_current.toordinal() - 1)
    start = date(end.year, end.month, 1)
    return start, end


def generate_previous_month_reports(now: datetime | None = None, force: bool = False) -> tuple[Path, Path] | None:
    local_now = (now or datetime.now(CAIRO)).astimezone(CAIRO)
    if local_now.day != 1 and not force:
        return None

    start_date, end_date = previous_month_period(local_now.date())
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"MY_HR_Report_{start_date.year}_{start_date.month:02d}"
    xlsx_path = REPORT_DIR / f"{stem}.xlsx"
    pdf_path = REPORT_DIR / f"{stem}.pdf"

    with SessionLocal() as db:
        workbook = build_hr_workbook(db, start_date, end_date)
        pdf = build_hr_pdf(db, start_date, end_date)

    xlsx_temp = xlsx_path.with_suffix(".xlsx.tmp")
    pdf_temp = pdf_path.with_suffix(".pdf.tmp")
    xlsx_temp.write_bytes(workbook)
    pdf_temp.write_bytes(pdf)
    xlsx_temp.replace(xlsx_path)
    pdf_temp.replace(pdf_path)
    return xlsx_path, pdf_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Generate the previous-month report regardless of day of month.")
    args = parser.parse_args()
    result = generate_previous_month_reports(force=args.force)
    if result is None:
        print("Monthly report is scheduled for day 1 at 09:00 Cairo time; no report generated today.")
        return 0
    print(f"Excel report saved: {result[0]}")
    print(f"PDF report saved: {result[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
