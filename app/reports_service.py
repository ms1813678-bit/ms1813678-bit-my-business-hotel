"""Local Excel reports for employees, attendance, leave, advances, staff actions and payroll."""
from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import (
    AttendanceDailyRecord,
    Department,
    Employee,
    LeaveRecord,
    LeaveType,
    PayrollLine,
    PayrollPayment,
    PayrollPeriod,
)
from app.models_staff_finance import SalaryAdvance, StaffAction


def _write_sheet(workbook: Workbook, title: str, headers: list[str], rows: list[tuple]) -> None:
    sheet = workbook.create_sheet(title=title[:31])
    sheet.append(headers)
    for row in rows:
        sheet.append(list(row))
    sheet.freeze_panes = "A2"
    if sheet.dimensions:
        sheet.auto_filter.ref = sheet.dimensions
    sheet.row_dimensions[1].height = 28
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="24415E")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for column_cells in sheet.columns:
        max_length = max((len(str(cell.value or "")) for cell in column_cells), default=10)
        sheet.column_dimensions[get_column_letter(column_cells[0].column)].width = min(
            max(max_length + 2, 12), 36
        )
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def _money(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def build_hr_workbook(db: Session, start_date: date, end_date: date) -> bytes:
    """Build a multi-sheet Excel workbook covering the main HR domains for the date range."""
    if end_date < start_date:
        raise ValueError("end_date_before_start_date")

    workbook = Workbook()
    workbook.remove(workbook.active)

    employees = db.scalars(select(Employee).order_by(Employee.code)).all()
    employee_map = {e.id: e for e in employees}
    dept_map = {
        d.id: d.name
        for d in db.scalars(select(Department)).all()
    }

    _write_sheet(
        workbook,
        "Employees",
        [
            "Employee code",
            "Full name",
            "Department",
            "Position",
            "Hire date",
            "Status",
            "Basic salary",
            "Daily hours",
            "Phone",
        ],
        [
            (
                emp.code,
                emp.full_name,
                dept_map.get(emp.department_id, ""),
                emp.position,
                emp.hire_date,
                emp.status,
                _money(emp.basic_salary),
                _money(emp.daily_hours),
                emp.phone or "",
            )
            for emp in employees
        ],
    )

    attendance = db.scalars(
        select(AttendanceDailyRecord)
        .where(
            AttendanceDailyRecord.work_date >= start_date,
            AttendanceDailyRecord.work_date <= end_date,
        )
        .order_by(AttendanceDailyRecord.work_date, AttendanceDailyRecord.employee_id)
    ).all()
    _write_sheet(
        workbook,
        "Attendance",
        [
            "Work date",
            "Employee code",
            "Employee",
            "Status",
            "Scheduled in",
            "Scheduled out",
            "Approved in",
            "Approved out",
            "Worked minutes",
            "Review notes",
        ],
        [
            (
                rec.work_date,
                employee_map[rec.employee_id].code if rec.employee_id in employee_map else "",
                employee_map[rec.employee_id].full_name if rec.employee_id in employee_map else "",
                rec.status,
                rec.scheduled_start_at,
                rec.scheduled_end_at,
                rec.approved_clock_in,
                rec.approved_clock_out,
                rec.worked_minutes,
                rec.review_notes or "",
            )
            for rec in attendance
        ],
    )

    leave_types = {
        lt.id: lt.name for lt in db.scalars(select(LeaveType)).all()
    }
    leave_records = db.scalars(
        select(LeaveRecord)
        .where(LeaveRecord.start_date <= end_date, LeaveRecord.end_date >= start_date)
        .order_by(LeaveRecord.start_date, LeaveRecord.employee_id)
    ).all()
    _write_sheet(
        workbook,
        "Leave",
        [
            "Employee code",
            "Employee",
            "Leave type",
            "Start",
            "End",
            "Days",
            "Status",
            "Reason",
        ],
        [
            (
                employee_map[item.employee_id].code if item.employee_id in employee_map else "",
                employee_map[item.employee_id].full_name if item.employee_id in employee_map else "",
                leave_types.get(item.leave_type_id, ""),
                item.start_date,
                item.end_date,
                _money(item.days),
                item.status,
                getattr(item, "reason", None) or "",
            )
            for item in leave_records
        ],
    )

    advances = db.scalars(
        select(SalaryAdvance)
        .where(
            SalaryAdvance.request_date >= start_date,
            SalaryAdvance.request_date <= end_date,
        )
        .order_by(SalaryAdvance.request_date, SalaryAdvance.employee_id)
    ).all()
    _write_sheet(
        workbook,
        "Advances",
        [
            "Request date",
            "Employee code",
            "Employee",
            "Payroll year",
            "Payroll month",
            "Amount",
            "Status",
            "Reason",
        ],
        [
            (
                adv.request_date,
                employee_map[adv.employee_id].code if adv.employee_id in employee_map else "",
                employee_map[adv.employee_id].full_name if adv.employee_id in employee_map else "",
                adv.payroll_year,
                adv.payroll_month,
                _money(adv.amount),
                adv.status,
                adv.reason or "",
            )
            for adv in advances
        ],
    )

    actions = db.scalars(
        select(StaffAction)
        .where(
            StaffAction.action_date >= start_date,
            StaffAction.action_date <= end_date,
        )
        .order_by(StaffAction.action_date, StaffAction.employee_id)
    ).all()
    _write_sheet(
        workbook,
        "Sanctions rewards",
        [
            "Action date",
            "Employee code",
            "Employee",
            "Kind",
            "Category",
            "Amount",
            "Payroll year",
            "Payroll month",
            "Status",
            "Reason",
        ],
        [
            (
                act.action_date,
                employee_map[act.employee_id].code if act.employee_id in employee_map else "",
                employee_map[act.employee_id].full_name if act.employee_id in employee_map else "",
                act.action_kind,
                act.category,
                _money(act.amount),
                act.payroll_year,
                act.payroll_month,
                act.status,
                act.reason or "",
            )
            for act in actions
        ],
    )

    payroll_rows = db.execute(
        select(PayrollPeriod, PayrollLine, Employee)
        .join(PayrollLine, PayrollLine.payroll_period_id == PayrollPeriod.id)
        .join(Employee, Employee.id == PayrollLine.employee_id)
        .where(
            PayrollPeriod.year * 100 + PayrollPeriod.month >= start_date.year * 100 + start_date.month,
            PayrollPeriod.year * 100 + PayrollPeriod.month <= end_date.year * 100 + end_date.month,
        )
        .order_by(PayrollPeriod.year, PayrollPeriod.month, Employee.code)
    ).all()

    payment_sums: dict[int, Decimal] = dict(
        db.execute(
            select(
                PayrollPayment.payroll_line_id,
                func.coalesce(func.sum(PayrollPayment.amount), 0),
            ).group_by(PayrollPayment.payroll_line_id)
        ).all()
    )

    _write_sheet(
        workbook,
        "Payroll",
        [
            "Year",
            "Month",
            "Period status",
            "Employee code",
            "Employee",
            "Base pay",
            "Overtime pay",
            "Absence deduction",
            "Lateness deduction",
            "Advance deduction",
            "Other deductions",
            "Gross pay",
            "Net pay",
            "Payment status",
            "Paid amount",
            "Remaining",
        ],
        [
            (
                period.year,
                period.month,
                period.status,
                emp.code,
                emp.full_name,
                _money(getattr(line, "base_pay", None) or getattr(line, "base_salary", None)),
                _money(getattr(line, "overtime_pay", None)),
                _money(getattr(line, "absence_deduction", None)),
                _money(getattr(line, "lateness_deduction", None)),
                _money(getattr(line, "advance_deduction", None)),
                _money(getattr(line, "other_deductions", None)),
                _money(getattr(line, "gross_pay", None)),
                _money(getattr(line, "net_pay", None)),
                getattr(line, "payment_status", ""),
                _money(payment_sums.get(line.id, Decimal("0"))),
                _money(
                    max(
                        Decimal("0"),
                        (getattr(line, "net_pay", None) or Decimal("0"))
                        - payment_sums.get(line.id, Decimal("0")),
                    )
                ),
            )
            for period, line, emp in payroll_rows
        ],
    )

    department_rows = db.execute(
        select(
            Department.name,
            func.count(Employee.id),
            func.coalesce(func.sum(Employee.basic_salary), 0),
        )
        .outerjoin(
            Employee,
            and_(Employee.department_id == Department.id, Employee.status == "active"),
        )
        .group_by(Department.id, Department.name)
        .order_by(Department.name)
    ).all()
    _write_sheet(
        workbook,
        "Departments",
        ["Department", "Active employee count", "Current monthly basic payroll"],
        [(name, count, float(total or 0)) for name, count, total in department_rows],
    )

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def validate_workbook(data: bytes) -> list[str]:
    """Return sheet names from a generated workbook (used by tests)."""
    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    try:
        return list(workbook.sheetnames)
    finally:
        workbook.close()
