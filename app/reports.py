"""Local Excel reports for HR, attendance, leave, advances, staff actions and payroll."""
from datetime import date, datetime, time, timedelta
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


def _write_sheet(workbook: Workbook, title: str, headers: list[str], rows: list[tuple]) -> None:
    sheet = workbook.create_sheet(title=title[:31])
    sheet.append(headers)
    for row in rows:
        sheet.append(list(row))
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
