"""Manual HR report generator for administrators.

This script intentionally does not schedule reports or send email automatically.
The web Reports module is the primary UI; this CLI mirrors its manual date and
employee filters for local/offline use.
"""
import argparse
from datetime import date
from pathlib import Path

from app.db import SessionLocal
from app.reports import build_hr_pdf, build_hr_workbook

REPORT_DIR = Path("var") / "reports"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--employee-id", type=int, default=None, help="Optional employee database ID")
    args = parser.parse_args()

    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)
    if end_date < start_date:
        parser.error("--end-date must be on or after --start-date")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"_employee_{args.employee_id}" if args.employee_id else "_all_employees"
    stem = f"MY_HR_Report_{start_date.isoformat()}_{end_date.isoformat()}{suffix}"
    xlsx_path = REPORT_DIR / f"{stem}.xlsx"
    pdf_path = REPORT_DIR / f"{stem}.pdf"

    with SessionLocal() as db:
        workbook = build_hr_workbook(db, start_date, end_date, employee_id=args.employee_id)
        pdf = build_hr_pdf(db, start_date, end_date, employee_id=args.employee_id)

    xlsx_temp = xlsx_path.with_suffix(".xlsx.tmp")
    pdf_temp = pdf_path.with_suffix(".pdf.tmp")
    xlsx_temp.write_bytes(workbook)
    pdf_temp.write_bytes(pdf)
    xlsx_temp.replace(xlsx_path)
    pdf_temp.replace(pdf_path)

    print(f"Excel report saved: {xlsx_path}")
    print(f"PDF report saved: {pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
