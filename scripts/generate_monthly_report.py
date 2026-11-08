"""Generate the previous month's HR reports locally on the first day of each month."""
import argparse
import logging
import smtplib
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from app.db import SessionLocal
from app.reports import build_hr_pdf, build_hr_workbook
from app.config import settings

logger = logging.getLogger(__name__)


CAIRO = ZoneInfo("Africa/Cairo")
REPORT_DIR = Path("var") / "reports"


def previous_month_period(today: date) -> tuple[date, date]:
    first_of_current = date(today.year, today.month, 1)
    end = date.fromordinal(first_of_current.toordinal() - 1)
    start = date(end.year, end.month, 1)
    return start, end


def _email_reports(paths: tuple[Path, Path]) -> None:
    if not settings.report_email_to:
        logger.warning("Monthly reports saved locally; REPORT_EMAIL_TO is not configured.")
        return
    if not settings.report_smtp_host:
        logger.warning("Monthly reports saved locally; REPORT_SMTP_HOST is not configured.")
        return

    message = EmailMessage()
    message["Subject"] = "M.Y. Business Hotel HR - Monthly Report"
    message["From"] = settings.report_smtp_username or settings.report_email_to
    message["To"] = settings.report_email_to
    message.set_content(
        "The previous month's M.Y. Business Hotel HR reports are attached."
    )
    for path in paths:
        message.add_attachment(
            path.read_bytes(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet" if path.suffix == ".xlsx" else "pdf",
            filename=path.name,
        )

    with smtplib.SMTP(settings.report_smtp_host, settings.report_smtp_port, timeout=30) as smtp:
        smtp.ehlo()
        if settings.report_smtp_starttls:
            smtp.starttls()
            smtp.ehlo()
        if settings.report_smtp_username:
            smtp.login(settings.report_smtp_username, settings.report_smtp_password)
        smtp.send_message(message)


def generate_previous_month_reports(now: datetime | None = None, force: bool = False) -> tuple[Path, Path] | None:
    local_now = (now or datetime.now(CAIRO)).astimezone(CAIRO)
    if not force and (local_now.day != 1 or local_now.hour < 9):
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

    try:
        _email_reports((xlsx_path, pdf_path))
    except Exception as exc:
        # Never remove or invalidate local reports because email delivery failed.
        logger.exception("Monthly report email failed: %s", exc)

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
