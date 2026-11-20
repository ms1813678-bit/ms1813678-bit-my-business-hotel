from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import BytesIO

from openpyxl import load_workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    AdvanceInstallment,
    AdvanceRequest,
    AdminAccount,
    AttendanceDailyRecord,
    AttendanceImportBatch,
    AttendanceMonth,
    Department,
    Employee,
    EmployeeServicePeriod,
    LeaveBalanceTransaction,
    LeaveRecord,
    LeaveType,
    PayrollItem,
    PayrollPayment,
    PayrollRun,
    SecurityShiftAssignment,
    ShiftPattern,
    StaffAction,
)
from app.advance_workflow import (
    approve_advance,
    create_advance,
    mark_advance_paid,
)
from app.attendance_workflow import (
    add_manual_attendance,
    approve_attendance_day,
    approve_attendance_month,
)
from app.leave_workflow import add_balance_entry, confirm_leave, leave_balance
from app.payroll_workflow import (
    add_payroll_payment,
    approve_payroll_run,
    close_payroll_run,
    create_payroll_run,
    payment_totals,
)
from app.reports import build_hr_workbook
from app.security_roster_workflow import confirm_security_roster_day
from app.staff_actions_workflow import (
    approve_staff_action,
    create_staff_action,
    record_employee_acknowledgement,
)


class _FixedToday(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 20)


def test_complete_synthetic_hr_to_payroll_acceptance_flow(monkeypatch):
    """Exercise one connected, synthetic HR-to-payment flow on an isolated database."""
    import app.advance_workflow as advance_workflow

    monkeypatch.setattr(advance_workflow, "date", _FixedToday)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    actor = "synthetic-hr-admin"

    try:
        with Session(engine, expire_on_commit=False) as db:
            front_office = Department(name="Front Office", active=True)
            security = Department(name="Security", active=True)
            db.add_all([front_office, security])
            db.flush()

            employee = Employee(
                code="MY-9001",
                full_name="Synthetic Front Office Employee",
                department_id=front_office.id,
                position="Front Office Agent",
                hire_date=date(2026, 11, 29),
                status="active",
                basic_salary=Decimal("60000.00"),
                daily_hours=Decimal("8.00"),
            )
            guard_day = Employee(
                code="MY-9002",
                full_name="Synthetic Security Day Guard",
                department_id=security.id,
                position="Security Guard",
                hire_date=date(2026, 11, 30),
                status="active",
                basic_salary=Decimal("36000.00"),
                daily_hours=Decimal("12.00"),
            )
            guard_night = Employee(
                code="MY-9003",
                full_name="Synthetic Security Night Guard",
                department_id=security.id,
                position="Security Guard",
                hire_date=date(2026, 11, 30),
                status="active",
                basic_salary=Decimal("36000.00"),
                daily_hours=Decimal("12.00"),
            )
            db.add_all([employee, guard_day, guard_night])
            db.flush()

            db.add_all([
                EmployeeServicePeriod(
                    employee_id=employee.id,
                    start_date=date(2026, 11, 29),
                    start_reason="Synthetic initial employment",
                ),
                EmployeeServicePeriod(
                    employee_id=guard_day.id,
                    start_date=date(2026, 11, 30),
                    start_reason="Synthetic initial employment",
                ),
                EmployeeServicePeriod(
                    employee_id=guard_night.id,
                    start_date=date(2026, 11, 30),
                    start_reason="Synthetic initial employment",
                ),
            ])

            admin = AdminAccount(username=actor, password_hash="synthetic-test-hash")
            db.add(admin)

            security_day_pattern = ShiftPattern(
                department_id=security.id,
                code="SEC-08-20",
                name="Security day 08:00-20:00",
                start_time=time(8, 0),
                end_time=time(20, 0),
                match_before_minutes=30,
                match_after_minutes=30,
                active=True,
            )
            security_night_pattern = ShiftPattern(
                department_id=security.id,
                code="SEC-20-08",
                name="Security night 20:00-08:00",
                start_time=time(20, 0),
                end_time=time(8, 0),
                match_before_minutes=30,
                match_after_minutes=30,
                active=True,
            )
            db.add_all([security_day_pattern, security_night_pattern])
            db.flush()

            # Approved annual leave explains the one non-attendance day.
            annual_leave = LeaveType(
                code="ANNUAL",
                name_ar="إجازة سنوية",
                name_en="Annual leave",
                is_paid=True,
                tracks_balance=True,
                active=True,
            )
            db.add(annual_leave)
            db.flush()
            add_balance_entry(
                db,
                employee_id=employee.id,
                leave_type=annual_leave,
                transaction_type="opening",
                days_delta=Decimal("2.00"),
                reason="Synthetic opening balance",
                actor=actor,
            )
            leave = LeaveRecord(
                employee_id=employee.id,
                leave_type_id=annual_leave.id,
                start_date=date(2026, 11, 29),
                end_date=date(2026, 11, 29),
                calendar_days=1,
                charged_days=Decimal("1.00"),
                reason="Synthetic annual leave",
                status="draft",
                created_by=actor,
            )
            db.add(leave)
            db.flush()
            confirm_leave(db, leave, actor)
            assert leave_balance(db, employee.id, annual_leave.id) == Decimal("1.00")

            # Both required November source imports are present and retained.
            program_batch = AttendanceImportBatch(
                source_type="program_report",
                period_start=date(2026, 11, 1),
                period_end=date(2026, 11, 30),
                original_filename="synthetic-program.xlsx",
                file_sha256="a" * 64,
                original_file_bytes=b"synthetic-program-source",
                uploaded_by=actor,
                status="parsed",
                row_count=1,
                issue_count=0,
            )
            fingerprint_batch = AttendanceImportBatch(
                source_type="fingerprint",
                period_start=date(2026, 11, 1),
                period_end=date(2026, 11, 30),
                original_filename="synthetic-fingerprint.xlsx",
                file_sha256="b" * 64,
                original_file_bytes=b"synthetic-fingerprint-source",
                uploaded_by=actor,
                status="parsed",
                row_count=1,
                issue_count=0,
            )
            db.add_all([program_batch, fingerprint_batch])
            db.flush()
            month = AttendanceMonth(
                period_start=date(2026, 11, 1),
                period_end=date(2026, 11, 30),
                program_import_batch_id=program_batch.id,
                fingerprint_import_batch_id=fingerprint_batch.id,
                status="under_review",
                created_by=actor,
            )
            db.add(month)
            db.flush()

            # The two active guards cover exactly 24 hours using the approved
            # 12-hour shifts; HR explicitly confirms the proposed roster.
            roster = {
                guard_day.id: time(8, 0),
                guard_night.id: time(20, 0),
            }
            proposal = confirm_security_roster_day(
                db,
                date(2026, 11, 30),
                roster,
                admin,
            )
            assert proposal["status"] == "ready_for_admin_review"

            work_entries = [
                (
                    employee,
                    datetime(2026, 11, 30, 9, 0),
                    datetime(2026, 11, 30, 17, 0),
                    datetime(2026, 11, 30, 9, 0),
                    datetime(2026, 11, 30, 17, 0),
                    None,
                    "Manual front-office attendance supported by HR records",
                ),
                (
                    guard_day,
                    datetime(2026, 11, 30, 8, 0),
                    datetime(2026, 11, 30, 20, 0),
                    datetime(2026, 11, 30, 8, 0),
                    datetime(2026, 11, 30, 20, 0),
                    security_day_pattern,
                    "Manual day-guard attendance checked against roster",
                ),
                (
                    guard_night,
                    datetime(2026, 11, 30, 20, 0),
                    datetime(2026, 12, 1, 8, 0),
                    datetime(2026, 11, 30, 20, 0),
                    datetime(2026, 12, 1, 8, 0),
                    security_night_pattern,
                    "Manual overnight attendance checked against roster",
                ),
            ]
            for person, clock_in, clock_out, scheduled_in, scheduled_out, pattern, reason in work_entries:
                record = add_manual_attendance(
                    db,
                    employee_id=person.id,
                    work_date=date(2026, 11, 30),
                    clock_in=clock_in,
                    clock_out=clock_out,
                    reason=reason,
                    actor=actor,
                )
                record.scheduled_start_at = scheduled_in
                record.scheduled_end_at = scheduled_out
                if pattern is not None:
                    assignment = db.scalar(
                        select(SecurityShiftAssignment).where(
                            SecurityShiftAssignment.employee_id == person.id,
                            SecurityShiftAssignment.work_date == date(2026, 11, 30),
                        )
                    )
                    assert assignment is not None
                    record.security_shift_assignment_id = assignment.id
                approve_attendance_day(
                    db,
                    record_id=record.id,
                    clock_in=clock_in,
                    clock_out=clock_out,
                    actor=actor,
                    reason="Synthetic HR evidence reviewed and approved",
                )
                assert record.status == "approved"
                assert record.manual_entered_by == actor
                assert record.manual_entry_reason == reason

            approved_month = approve_attendance_month(
                db,
                month_id=month.id,
                actor=actor,
                reason="All synthetic source evidence and daily coverage reviewed",
                coverage_confirmed=True,
            )
            assert approved_month.status == "approved"
            assert program_batch.status == "approved"
            assert fingerprint_batch.status == "approved"

            # A 6,000 EGP installment advance requested in November starts
            # its accounting schedule in November and pays on the next month's 5th.
            advance = create_advance(
                db,
                employee_id=employee.id,
                request_date=date(2026, 11, 19),
                amount=Decimal("6000.00"),
                reason="Synthetic installment advance",
                advance_type="installment",
                actor=actor,
            )
            approve_advance(db, advance, actor, installment_count=6)
            mark_advance_paid(db, advance, actor)
            installments = db.scalars(
                select(AdvanceInstallment)
                .where(AdvanceInstallment.advance_request_id == advance.id)
                .order_by(AdvanceInstallment.installment_number)
            ).all()
            assert len(installments) == 6
            assert installments[0].payroll_month == date(2026, 11, 1)
            assert installments[0].scheduled_payment_date == date(2026, 12, 5)
            assert all(item.amount == Decimal("1000.00") for item in installments)

            # Financial reward and sanction are manually entered and approved.
            reward = create_staff_action(
                db,
                employee_id=employee.id,
                kind="reward",
                category="financial_reward",
                title="Synthetic service reward",
                is_financial=True,
                amount=Decimal("200.00"),
                payroll_month=date(2026, 11, 1),
                reason="Synthetic approved reward",
                actor=actor,
            )
            approve_staff_action(db, reward, actor)
            sanction = create_staff_action(
                db,
                employee_id=employee.id,
                kind="sanction",
                category="salary_deduction",
                title="Synthetic documented deduction",
                is_financial=True,
                amount=Decimal("100.00"),
                payroll_month=date(2026, 11, 1),
                reason="Synthetic approved sanction",
                actor=actor,
            )
            approve_staff_action(db, sanction, actor)
            record_employee_acknowledgement(
                db,
                sanction,
                actor,
                "declined",
                "Synthetic record: employee declined to sign acknowledgement",
            )
            assert sanction.acknowledgement_status == "declined"

            # Payroll may be created only after exact-month attendance approval.
            run = create_payroll_run(db, year=2026, month=11, actor=actor)
            assert run.status == "draft"
            assert run.pay_date == date(2026, 12, 5)
            employee_item = db.scalar(
                select(PayrollItem).where(
                    PayrollItem.payroll_run_id == run.id,
                    PayrollItem.employee_id == employee.id,
                )
            )
            assert employee_item is not None
            assert employee_item.base_salary == Decimal("4000.00")
            assert employee_item.advance_deduction == Decimal("1000.00")
            assert employee_item.staff_reward_amount == Decimal("200.00")
            assert employee_item.staff_sanction_amount == Decimal("100.00")
            assert employee_item.net_pay == Decimal("3100.00")

            approve_payroll_run(db, run, actor)
            close_payroll_run(db, run, actor)
            assert run.status == "closed"
            assert installments[0].status == "applied"
            assert installments[0].applied_payroll_run_id == run.id
            assert all(item.status == "scheduled" for item in installments[1:])
            assert reward.payroll_applied_at is not None
            assert sanction.payroll_applied_at is not None

            payroll_items = db.scalars(
                select(PayrollItem).where(PayrollItem.payroll_run_id == run.id)
            ).all()
            for item in payroll_items:
                add_payroll_payment(
                    db,
                    item,
                    amount=Decimal(item.net_pay),
                    paid_date=run.pay_date,
                    method="bank_transfer",
                    reference=f"SYNTHETIC-PAY-{item.employee_code_snapshot}",
                    actor=actor,
                )
                paid, due, status = payment_totals(db, item)
                assert paid == Decimal(item.net_pay)
                assert due == Decimal("0.00")
                assert status == "fully_paid"

            report_bytes = build_hr_workbook(
                db,
                date(2026, 11, 1),
                date(2026, 11, 30),
                employee_id=employee.id,
            )
            workbook = load_workbook(BytesIO(report_bytes), read_only=True)
            assert {
                "Employees",
                "Attendance",
                "Leave",
                "Leave balances",
                "Sanctions rewards",
                "Advances",
                "Payroll",
                "Departments",
            }.issubset(set(workbook.sheetnames))
            payroll_headers = [cell.value for cell in workbook["Payroll"][1]]
            assert "Paid" in payroll_headers
            assert "Remaining" in payroll_headers

            # The evidence and every financial transition are retained in the DB.
            assert db.scalar(
                select(AttendanceDailyRecord.id).where(
                    AttendanceDailyRecord.employee_id == employee.id,
                    AttendanceDailyRecord.manual_entered_by == actor,
                    AttendanceDailyRecord.status == "approved",
                )
            ) is not None
            assert db.scalar(
                select(LeaveBalanceTransaction.id).where(
                    LeaveBalanceTransaction.employee_id == employee.id,
                    LeaveBalanceTransaction.transaction_type == "leave_use",
                )
            ) is not None
            assert db.scalar(
                select(PayrollPayment.id).where(
                    PayrollPayment.payroll_item_id == employee_item.id
                )
            ) is not None
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
