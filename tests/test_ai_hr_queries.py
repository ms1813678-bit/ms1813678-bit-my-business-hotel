from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    AttendanceDailyRecord,
    Department,
    Employee,
    LeaveBalanceTransaction,
    LeaveType,
    AdvanceRequest,
    AdvanceInstallment,
    StaffAction,
)
import app.ai_hr_queries as queries


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, factory


def test_request_detectors():
    assert queries.is_attendance_analysis_request("حلّل حضور يوم 2026-11-02")
    assert queries.is_leave_balance_request("رصيد إجازة الموظف MY-0001")
    assert queries.is_advance_query_request("سلف وأقساط المتبقية لـ MY-0001")
    assert queries.is_staff_action_query_request("جزاءات شهر 2026-11")
    assert not queries.is_attendance_analysis_request("قارن جدول الأمن مع البصمة")


def test_attendance_analysis_flags_late_and_missing(monkeypatch):
    engine, factory = _session()
    try:
        with factory() as db:
            dept = Department(name="Reception", active=True)
            db.add(dept)
            db.flush()
            emp = Employee(
                code="MY-0001", full_name="موظف تجريبي", department_id=dept.id,
                position="Reception", hire_date=date(2026, 1, 1), status="active",
                basic_salary=Decimal("5000"), daily_hours=Decimal("8"),
            )
            db.add(emp)
            db.flush()
            db.add(AttendanceDailyRecord(
                employee_id=emp.id,
                work_date=date(2026, 11, 2),
                scheduled_start_at=datetime(2026, 11, 2, 9, 0),
                scheduled_end_at=datetime(2026, 11, 2, 17, 0),
                first_clock_in=datetime(2026, 11, 2, 9, 20),
                last_clock_out=None,
                status="needs_review",
            ))
            db.commit()

        monkeypatch.setattr(queries, "SessionLocal", factory)
        report = queries.build_attendance_analysis(work_date=date(2026, 11, 2), month_start=None)
        assert report["flagged_rows"] == 1
        text = queries.format_attendance_analysis_ar(report)
        assert "تأخير" in text or "انصراف" in text or "ملاحظات" in text
    finally:
        engine.dispose()


def test_leave_balance_report(monkeypatch):
    engine, factory = _session()
    try:
        with factory() as db:
            dept = Department(name="Reception", active=True)
            db.add(dept)
            db.flush()
            emp = Employee(
                code="MY-0002", full_name="موظف إجازة", department_id=dept.id,
                position="Reception", hire_date=date(2026, 1, 1), status="active",
                basic_salary=Decimal("5000"), daily_hours=Decimal("8"),
            )
            leave_type = LeaveType(
                code="annual", name_ar="سنوية", name_en="Annual",
                is_paid=True, tracks_balance=True, active=True,
            )
            db.add_all([emp, leave_type])
            db.flush()
            db.add(LeaveBalanceTransaction(
                employee_id=emp.id, leave_type_id=leave_type.id,
                transaction_type="opening", days_delta=Decimal("15"),
                reason="رصيد افتتاحي", created_by="test",
            ))
            db.commit()

        monkeypatch.setattr(queries, "SessionLocal", factory)
        report = queries.build_leave_balance_report("رصيد إجازة MY-0002")
        assert report["employees"]
        assert report["employees"][0]["balances"][0]["days"] == "15.00"
        text = queries.format_leave_balance_ar(report)
        assert "سنوية" in text
    finally:
        engine.dispose()


def test_advance_and_staff_action_reports(monkeypatch):
    engine, factory = _session()
    try:
        with factory() as db:
            dept = Department(name="Reception", active=True)
            db.add(dept)
            db.flush()
            emp = Employee(
                code="MY-0003", full_name="موظف سلفة", department_id=dept.id,
                position="Reception", hire_date=date(2026, 1, 1), status="active",
                basic_salary=Decimal("6000"), daily_hours=Decimal("8"),
            )
            db.add(emp)
            db.flush()
            req = AdvanceRequest(
                employee_id=emp.id, request_date=date(2026, 11, 16),
                payroll_month=date(2026, 11, 1), amount=Decimal("1200"),
                advance_type="installment", salary_basis=Decimal("6000"),
                reason="سلفة تجريبية", status="approved", created_by="test",
            )
            db.add(req)
            db.flush()
            now = datetime.utcnow()
            db.add(AdvanceInstallment(
                advance_request_id=req.id, installment_number=1,
                amount=Decimal("600"), payroll_month=date(2026, 12, 1),
                scheduled_payment_date=date(2026, 12, 5), status="scheduled",
                created_at=now,
            ))
            db.add(AdvanceInstallment(
                advance_request_id=req.id, installment_number=2,
                amount=Decimal("600"), payroll_month=date(2027, 1, 1),
                scheduled_payment_date=date(2027, 1, 5), status="scheduled",
                created_at=now,
            ))
            db.add(StaffAction(
                employee_id=emp.id, kind="sanction", category="lateness",
                title="تأخير", is_financial=True, amount=Decimal("50"),
                payroll_month=date(2026, 11, 1), reason="تأخير", status="approved",
                created_by="test",
            ))
            db.commit()

        monkeypatch.setattr(queries, "SessionLocal", factory)
        adv = queries.build_advance_report("سلف MY-0003")
        assert adv["items"]
        assert adv["items"][0]["remaining_installments"] == 2
        staff = queries.build_staff_actions_report("جزاءات 2026-11")
        assert staff["items"]
        assert staff["items"][0]["kind"] == "sanction"
    finally:
        engine.dispose()


def test_router_answers_without_model(monkeypatch):
    engine, factory = _session()
    try:
        monkeypatch.setattr(queries, "SessionLocal", factory)
        text = queries.answer_structured_hr_query("رصيد إجازة بدون موظف")
        assert "حدد الموظف" in text or "أرصدة" in text or "لا توجد"
    finally:
        engine.dispose()
