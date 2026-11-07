"""Local Excel reports for HR, attendance, leave, advances, staff actions and payroll."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import (
    AdvanceRequest,
    AttendanceDailyRecord,
    Department,
    Employee,
    LeaveBalanceTransaction,
    LeaveRecord,
    LeaveType,
    PayrollItem,
    PayrollPayment,
    PayrollRun,
    StaffAction,
)


def _filtered_date(column, start_date: date, end_date: date):
    return (column >= start_date, column <= end_date)


def _excel_safe_value(value):
    """Keep user-controlled strings literal when opened in spreadsheet software."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _write_sheet(workbook: Workbook, title: str, headers: list[str], rows: list[tuple]) -> None:
    sheet = workbook.create_sheet(title=title[:31])
    sheet.append(headers)
    for row in rows:
        sheet.append([_excel_safe_value(value) for value in row])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.row_dimensions[1].height = 28
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="24415E")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for column_cells in sheet.columns:
        max_length = max((len(str(cell.value or "")) for cell in column_cells), default=10)
        sheet.column_dimensions[get_column_letter(column_cells[0].column)].width = min(max(max_length + 2, 12), 34)
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def build_hr_workbook(db: Session, start_date: date, end_date: date) -> bytes:
    if end_date < start_date:
        raise ValueError("End date must not precede start date.")

    workbook = Workbook()
    workbook.remove(workbook.active)

    employees = db.scalars(select(Employee).order_by(Employee.code)).all()
    _write_sheet(
        workbook,
        "Employees",
        ["Employee code", "Full name", "Department", "Position", "Hire date", "Status", "Basic salary", "Daily hours", "Phone"],
        [
            (
                employee.code,
                employee.full_name,
                employee.department.name if employee.department else "",
                employee.position,
                employee.hire_date,
                employee.status,
                float(employee.basic_salary),
                float(employee.daily_hours),
                employee.phone or "",
            )
            for employee in employees
        ],
    )

    attendance = db.scalars(
        select(AttendanceDailyRecord)
        .where(*_filtered_date(AttendanceDailyRecord.work_date, start_date, end_date))
        .order_by(AttendanceDailyRecord.work_date, AttendanceDailyRecord.employee_id)
    ).all()
    employee_map = {employee.id: employee for employee in employees}
    _write_sheet(
        workbook,
        "Attendance",
        ["Work date", "Employee code", "Employee", "Status", "Scheduled in", "Scheduled out", "Approved in", "Approved out", "Worked minutes", "Review notes"],
        [
            (
                record.work_date,
                employee_map[record.employee_id].code if record.employee_id in employee_map else "",
                employee_map[record.employee_id].full_name if record.employee_id in employee_map else "",
                record.status,
                record.scheduled_start_at,
                record.scheduled_end_at,
                record.approved_clock_in,
                record.approved_clock_out,
                record.worked_minutes,
                record.review_notes or "",
            )
            for record in attendance
        ],
    )

    leave_records = db.scalars(
        select(LeaveRecord)
        .where(LeaveRecord.start_date <= end_date, LeaveRecord.end_date >= start_date)
        .order_by(LeaveRecord.start_date, LeaveRecord.employee_id)
    ).all()
    leave_types = {
        item.id: item.name_ar
        for item in db.scalars(select(LeaveType)).all()
    }
    _write_sheet(
        workbook,
        "Leave",
        ["Employee code", "Employee", "Leave type", "Start", "End", "Calendar days", "Charged days", "Status", "Reason"],
        [
            (
                employee_map[item.employee_id].code if item.employee_id in employee_map else "",
                employee_map[item.employee_id].full_name if item.employee_id in employee_map else "",
                leave_types.get(item.leave_type_id, ""),
                item.start_date,
                item.end_date,
                item.calendar_days,
                float(item.charged_days),
                item.status,
                item.reason,
            )
            for item in leave_records
        ],
    )

    balance_transactions = db.scalars(
        select(LeaveBalanceTransaction)
        .where(LeaveBalanceTransaction.created_at >= datetime.combine(start_date, time.min), LeaveBalanceTransaction.created_at < datetime.combine(end_date + timedelta(days=1), time.min))
        .order_by(LeaveBalanceTransaction.created_at, LeaveBalanceTransaction.id)
    ).all()
    _write_sheet(
        workbook,
        "Leave balances",
        ["Date", "Employee code", "Employee", "Leave type ID", "Transaction", "Days delta", "Reason", "Recorded by"],
        [
            (
                item.created_at,
                employee_map[item.employee_id].code if item.employee_id in employee_map else "",
                employee_map[item.employee_id].full_name if item.employee_id in employee_map else "",
                item.leave_type_id,
                item.transaction_type,
                float(item.days_delta),
                item.reason,
                item.created_by,
            )
            for item in balance_transactions
        ],
    )

    actions = db.scalars(
        select(StaffAction)
        .where(StaffAction.created_at >= datetime.combine(start_date, time.min), StaffAction.created_at < datetime.combine(end_date + timedelta(days=1), time.min))
        .order_by(StaffAction.created_at, StaffAction.id)
    ).all()
    _write_sheet(
        workbook,
        "Sanctions rewards",
        ["Date", "Employee code", "Employee", "Type", "Category", "Title", "Financial", "Amount", "Payroll month", "Status", "Reason"],
        [
            (
                item.created_at,
                employee_map[item.employee_id].code if item.employee_id in employee_map else "",
                employee_map[item.employee_id].full_name if item.employee_id in employee_map else "",
                item.kind,
                item.category,
                item.title,
                item.is_financial,
                float(item.amount) if item.amount is not None else None,
                item.payroll_month,
                item.status,
                item.reason,
            )
            for item in actions
        ],
    )

    advances = db.scalars(
        select(AdvanceRequest)
        .where(*_filtered_date(AdvanceRequest.request_date, start_date, end_date))
        .order_by(AdvanceRequest.request_date, AdvanceRequest.employee_id)
    ).all()
    _write_sheet(
        workbook,
        "Advances",
        ["Request date", "Employee code", "Employee", "Payroll month", "Amount", "Salary basis", "Status", "Paid at", "Deducted at", "Reason"],
        [
            (
                item.request_date,
                employee_map[item.employee_id].code if item.employee_id in employee_map else "",
                employee_map[item.employee_id].full_name if item.employee_id in employee_map else "",
                item.payroll_month,
                float(item.amount),
                float(item.salary_basis),
                item.status,
                item.paid_at,
                item.deducted_at,
                item.reason,
            )
            for item in advances
        ],
    )

    payroll_rows = db.execute(
        select(PayrollRun, PayrollItem, Employee)
        .join(PayrollItem, PayrollItem.payroll_run_id == PayrollRun.id)
        .join(Employee, Employee.id == PayrollItem.employee_id)
        .where(PayrollRun.period_start <= end_date, PayrollRun.period_end >= start_date)
        .order_by(PayrollRun.period_start, Employee.code)
    ).all()
    payment_sums = dict(
        db.execute(
            select(PayrollPayment.payroll_item_id, func.coalesce(func.sum(PayrollPayment.amount), 0))
            .group_by(PayrollPayment.payroll_item_id)
        ).all()
    )
    _write_sheet(
        workbook,
        "Payroll",
        ["Month", "Pay date", "Run status", "Employee code", "Employee", "Basic", "Base after proration", "Attendance bonus", "Performance bonus", "Occupancy bonus", "Overtime", "Other bonus", "Absence deduction", "Lateness deduction", "Insurance", "Taxes", "Other deductions", "Advance", "Financial reward", "Financial sanction", "Net pay", "Paid", "Remaining"],
        [
            (
                run.period_start,
                run.pay_date,
                run.status,
                item.employee_code_snapshot,
                item.employee_name_snapshot,
                float(item.salary_basis),
                float(item.base_salary),
                float(item.attendance_bonus),
                float(item.performance_bonus),
                float(item.occupancy_bonus),
                float(item.overtime_amount),
                float(item.other_bonus),
                float(item.absence_deduction),
                float(item.lateness_deduction),
                float(item.insurance_share),
                float(item.taxes),
                float(item.other_deductions),
                float(item.advance_deduction),
                float(item.staff_reward_amount),
                float(item.staff_sanction_amount),
                float(item.net_pay),
                float(payment_sums.get(item.id, 0)),
                float(max(0, item.net_pay - payment_sums.get(item.id, 0))),
            )
            for run, item, employee in payroll_rows
        ],
    )

    # Period-level payroll summary makes month-to-month comparison possible
    # without changing the detailed employee-level Payroll sheet.
    monthly_totals = {}
    for run, item, employee in payroll_rows:
        summary = monthly_totals.setdefault(run.id, {
            "run": run,
            "count": 0,
            "gross": Decimal("0.00"),
            "deductions": Decimal("0.00"),
            "net": Decimal("0.00"),
            "paid": Decimal("0.00"),
            "fully_paid": 0,
            "partially_paid": 0,
            "unpaid": 0,
        })
        gross = sum((Decimal(getattr(item, field) or 0) for field in (
            "base_salary", "attendance_bonus", "performance_bonus",
            "occupancy_bonus", "overtime_amount", "other_bonus",
            "staff_reward_amount",
        )), Decimal("0.00"))
        deductions = sum((Decimal(getattr(item, field) or 0) for field in (
            "absence_deduction", "lateness_deduction", "insurance_share",
            "taxes", "other_deductions", "advance_deduction",
            "staff_sanction_amount",
        )), Decimal("0.00"))
        paid = Decimal(str(payment_sums.get(item.id, 0) or 0))
        remaining = max(Decimal("0.00"), Decimal(item.net_pay) - paid)
        summary["count"] += 1
        summary["gross"] += gross
        summary["deductions"] += deductions
        summary["net"] += Decimal(item.net_pay)
        summary["paid"] += paid
        if paid <= 0:
            summary["unpaid"] += 1
        elif remaining > 0:
            summary["partially_paid"] += 1
        else:
            summary["fully_paid"] += 1

    ordered_summaries = sorted(
        monthly_totals.values(), key=lambda row: row["run"].period_start
    )
    _write_sheet(
        workbook,
        "Payroll comparison",
        ["Month", "Pay date", "Run status", "Employees", "Gross earnings",
         "Deductions", "Net payroll", "Paid", "Remaining",
         "Fully paid employees", "Partially paid employees", "Unpaid employees"],
        [(
            row["run"].period_start,
            row["run"].pay_date,
            row["run"].status,
            row["count"],
            float(row["gross"]),
            float(row["deductions"]),
            float(row["net"]),
            float(row["paid"]),
            float(max(Decimal("0.00"), row["net"] - row["paid"])),
            row["fully_paid"],
            row["partially_paid"],
            row["unpaid"],
        ) for row in ordered_summaries],
    )

    department_rows = db.execute(
        select(
            Department.name,
            func.count(Employee.id),
            func.coalesce(func.sum(Employee.basic_salary), 0),
        )
        .outerjoin(Employee, and_(Employee.department_id == Department.id, Employee.status == "active"))
        .group_by(Department.id, Department.name)
        .order_by(Department.name)
    ).all()
    _write_sheet(
        workbook,
        "Departments",
        ["Department", "Employee count", "Current monthly basic payroll"],
        [(name, count, float(total)) for name, count, total in department_rows],
    )

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def validate_workbook(data: bytes) -> list[str]:
    workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    try:
        return workbook.sheetnames
    finally:
        workbook.close()



def _pdf_font_name() -> str:
    from pathlib import Path
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/tahoma.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    ]
    for font_path in candidates:
        if font_path.is_file():
            font_name = "MYHotelArabic"
            if font_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
            return font_name
    raise RuntimeError("An Arabic-capable system font is required to create the PDF report.")


def _pdf_cell(value, *, reshape_arabic: bool = True) -> str:
    from xml.sax.saxutils import escape
    text = "" if value is None else str(value)
    if reshape_arabic and any("\u0600" <= char <= "\u06ff" for char in text):
        import arabic_reshaper
        from bidi.algorithm import get_display
        text = get_display(arabic_reshaper.reshape(text))
    return escape(text).replace("\n", "<br/>")


def build_hr_pdf(db: Session, start_date: date, end_date: date) -> bytes:
    """Build a printable, Arabic-capable PDF companion to the complete Excel workbook."""
    if end_date < start_date:
        raise ValueError("End date must not precede start date.")

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A3, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle

    font_name = _pdf_font_name()
    workbook = load_workbook(BytesIO(build_hr_workbook(db, start_date, end_date)), read_only=True, data_only=True)
    output = BytesIO()
    page_size = landscape(A3)
    document = SimpleDocTemplate(
        output,
        pagesize=page_size,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"M.Y. Business Hotel HR Report {start_date} - {end_date}",
        author="M.Y. Business Hotel HR System",
    )
    base_styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "MYReportTitle", parent=base_styles["Title"], fontName=font_name,
        fontSize=16, leading=20, alignment=TA_RIGHT, spaceAfter=5 * mm,
    )
    section_style = ParagraphStyle(
        "MYReportSection", parent=base_styles["Heading2"], fontName=font_name,
        fontSize=11, leading=14, alignment=TA_RIGHT, spaceBefore=3 * mm, spaceAfter=2 * mm,
    )
    cell_style = ParagraphStyle(
        "MYReportCell", parent=base_styles["BodyText"], fontName=font_name,
        fontSize=6, leading=7.2, alignment=TA_RIGHT, wordWrap="CJK",
    )
    header_style = ParagraphStyle(
        "MYReportHeader", parent=cell_style, fontSize=6.5, leading=8,
        alignment=TA_CENTER, textColor=colors.white,
    )

    story = [
        Paragraph(_pdf_cell("M.Y. Business Hotel — تقرير الموارد البشرية"), title_style),
        Paragraph(_pdf_cell(f"الفترة من {start_date.strftime('%d/%m/%Y')} إلى {end_date.strftime('%d/%m/%Y')}"), section_style),
        Paragraph(_pdf_cell("هذا التقرير للعرض والطباعة فقط، ولا يغيّر أي بيانات داخل النظام."), cell_style),
        Spacer(1, 4 * mm),
    ]
    for sheet_index, sheet in enumerate(workbook.worksheets):
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            continue
        story.append(Paragraph(_pdf_cell(sheet.title), section_style))
        formatted = []
        for row_index, row in enumerate(rows):
            style = header_style if row_index == 0 else cell_style
            formatted.append([Paragraph(_pdf_cell(value), style) for value in row])
        col_count = max(1, len(rows[0]))
        available_width = page_size[0] - document.leftMargin - document.rightMargin
        widths = [available_width / col_count] * col_count
        table = LongTable(
            formatted,
            colWidths=widths,
            repeatRows=1,
            hAlign="RIGHT",
            splitByRow=1,
        )
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#24415E")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#AAB4BE")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(table)
        if sheet_index < len(workbook.worksheets) - 1:
            story.append(PageBreak())
    workbook.close()
    document.build(story)
    return output.getvalue()
