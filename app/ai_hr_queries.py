"""Read-only HR query reports for the local AI assistant.

These helpers never write to the database. They answer structured questions about
attendance review, leave balances, advances/installments, and staff actions.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select

from app.db import SessionLocal
from app.leave_workflow import leave_balance
from app.models import (
    AdvanceInstallment,
    AdvanceRequest,
    AttendanceDailyRecord,
    Employee,
    LeaveType,
    StaffAction,
)

_DATE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
_MONTH_RE = re.compile(r"(20\d{2})-(\d{2})")
_CODE_RE = re.compile(r"\b(MY[-\s]?\d{3,6}|SEC[-\s]?\d{1,4})\b", re.IGNORECASE)


def _parse_date(question: str, default: date | None = None) -> date:
    match = _DATE_RE.search(question or "")
    if match:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return default or date.today()


def _parse_month(question: str, default: date | None = None) -> date:
    """Return first day of month from YYYY-MM or YYYY-MM-DD."""
    full = _DATE_RE.search(question or "")
    if full:
        return date(int(full.group(1)), int(full.group(2)), 1)
    month = _MONTH_RE.search(question or "")
    if month:
        return date(int(month.group(1)), int(month.group(2)), 1)
    base = default or date.today()
    return date(base.year, base.month, 1)


def _month_end(month_start: date) -> date:
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1) - timedelta(days=1)
    return date(month_start.year, month_start.month + 1, 1) - timedelta(days=1)


def _find_employees(db, question: str) -> list[Employee]:
    """Resolve employees by code or name fragment mentioned in the question."""
    code_match = _CODE_RE.search(question or "")
    if code_match:
        raw = re.sub(r"\s+", "", code_match.group(1)).upper()
        if raw.startswith("MY") and not raw.startswith("MY-"):
            raw = "MY-" + raw[2:]
        if raw.startswith("SEC") and not raw.startswith("SEC-"):
            raw = "SEC-" + raw[3:]
        rows = db.scalars(select(Employee).where(Employee.code == raw)).all()
        if rows:
            return list(rows)

    q = question or ""
    name_hint = None
    for marker in ("للموظف", "موظف", "اسم", "للحارس", "حارس"):
        if marker in q:
            tail = q.split(marker, 1)[1].strip(" :：-")
            tail = re.split(r"\d{4}-\d{2}", tail)[0].strip(" ؟?.,")
            if tail and len(tail) >= 2:
                name_hint = tail
                break
    if name_hint:
        rows = db.scalars(
            select(Employee).where(Employee.full_name.ilike(f"%{name_hint}%")).limit(5)
        ).all()
        if rows:
            return list(rows)
    return []


def is_attendance_analysis_request(question: str) -> bool:
    q = " ".join((question or "").casefold().split())
    if not q:
        return False
    attendance = any(term in q for term in ("حضور", "انصراف", "تأخير", "غياب", "attendance"))
    analysis = any(
        term in q
        for term in (
            "تحليل", "حلّل", "حلل", "ملخص", "ملاحظات", "مراجعة",
            "مشاكل", "تقرير", "summary", "analyze",
        )
    )
    if "أمن" in q and "بصم" in q:
        return False
    return attendance and analysis


def is_leave_balance_request(question: str) -> bool:
    q = " ".join((question or "").casefold().split())
    return any(term in q for term in ("رصيد إجازة", "رصيد اجازة", "أرصدة الإجازات", "ارصدة الاجازات", "إجازات الموظف", "اجازات الموظف"))


def is_advance_query_request(question: str) -> bool:
    q = " ".join((question or "").casefold().split())
    return any(term in q for term in ("سلف", "سلفة", "أقساط", "اقساط", "قسط"))


def is_staff_action_query_request(question: str) -> bool:
    q = " ".join((question or "").casefold().split())
    return any(
        term in q
        for term in ("جزاء", "جزاءات", "مكافأة", "مكافات", "مكافآت", "عقوبة", "خصم تأديبي")
    )


def is_structured_hr_query(question: str) -> bool:
    return (
        is_attendance_analysis_request(question)
        or is_leave_balance_request(question)
        or is_advance_query_request(question)
        or is_staff_action_query_request(question)
    )


def _classify_attendance_row(record: AttendanceDailyRecord) -> list[str]:
    notes: list[str] = []
    if record.status in {"needs_review", "ready_for_review"}:
        notes.append(f"حالة السجل: {record.status}")
    if record.scheduled_start_at and record.first_clock_in:
        late = int((record.first_clock_in - record.scheduled_start_at).total_seconds() // 60)
        if late > 0:
            notes.append(f"تأخير حضور {late} دقيقة")
    if record.scheduled_end_at and record.last_clock_out:
        early = int((record.scheduled_end_at - record.last_clock_out).total_seconds() // 60)
        if early > 0:
            notes.append(f"انصراف مبكر {early} دقيقة")
    if record.first_clock_in is None and record.program_clock_in is None:
        notes.append("لا توجد بصمة/تسجيل دخول")
    if record.last_clock_out is None and record.program_clock_out is None:
        notes.append("لا توجد بصمة/تسجيل انصراف")
    if record.worked_minutes is not None and record.scheduled_start_at and record.scheduled_end_at:
        scheduled = int((record.scheduled_end_at - record.scheduled_start_at).total_seconds() // 60)
        if scheduled > 0 and abs(record.worked_minutes - scheduled) >= 60:
            notes.append(
                f"مدة عمل غير متوقعة ({record.worked_minutes} دقيقة مقابل {scheduled} مجدولة)"
            )
    if record.review_notes:
        notes.append(f"ملاحظة محفوظة: {record.review_notes}")
    return notes


def build_attendance_analysis(*, work_date: date | None, month_start: date | None) -> dict[str, Any]:
    with SessionLocal() as db:
        query = select(AttendanceDailyRecord, Employee).join(
            Employee, Employee.id == AttendanceDailyRecord.employee_id
        )
        if work_date is not None:
            query = query.where(AttendanceDailyRecord.work_date == work_date)
            period_label = work_date.isoformat()
        else:
            assert month_start is not None
            end = _month_end(month_start)
            query = query.where(
                AttendanceDailyRecord.work_date >= month_start,
                AttendanceDailyRecord.work_date <= end,
            )
            period_label = f"{month_start.isoformat()} → {end.isoformat()}"

        rows = db.execute(query.order_by(AttendanceDailyRecord.work_date, Employee.code)).all()
        items: list[dict[str, Any]] = []
        for record, employee in rows:
            flags = _classify_attendance_row(record)
            if not flags and record.status == "approved":
                continue
            if not flags:
                continue
            items.append({
                "employee_code": employee.code,
                "employee_name": employee.full_name,
                "work_date": record.work_date.isoformat(),
                "status": record.status,
                "notes": flags,
            })
        return {
            "period": period_label,
            "reviewed_rows": len(rows),
            "flagged_rows": len(items),
            "items": items,
            "note": "تقرير مراجعة فقط. لم يتم اعتماد أو رفض أي سجل حضور.",
        }


def format_attendance_analysis_ar(report: dict[str, Any]) -> str:
    lines = [
        f"تحليل حضور للفترة {report['period']}.",
        report["note"],
        f"سجلات مفحوصة: {report['reviewed_rows']} — تحتاج انتباه: {report['flagged_rows']}.",
    ]
    if not report["items"]:
        lines.append("لا توجد ملاحظات ظاهرة في السجلات المتاحة لهذه الفترة.")
        return "\n".join(lines)
    lines.append("التفاصيل:")
    for index, item in enumerate(report["items"][:40], start=1):
        lines.append(
            f"{index}) {item['employee_code']} — {item['employee_name']} — {item['work_date']} — {item['status']}"
        )
        for note in item["notes"]:
            lines.append(f"   - {note}")
    if report["flagged_rows"] > 40:
        lines.append(f"... و{report['flagged_rows'] - 40} سجلًا إضافيًا. راجع شاشة الحضور للتفاصيل الكاملة.")
    return "\n".join(lines)


def build_leave_balance_report(question: str) -> dict[str, Any]:
    with SessionLocal() as db:
        employees = _find_employees(db, question)
        types = db.scalars(
            select(LeaveType).where(LeaveType.active.is_(True)).order_by(LeaveType.id)
        ).all()
        if not types:
            return {"employees": [], "note": "لا توجد أنواع إجازات مفعّلة في النظام."}
        if not employees:
            return {
                "employees": [],
                "note": "حدد الموظف بالرمز (مثل MY-0001) أو بالاسم لعرض أرصدة الإجازات.",
            }
        result = []
        for employee in employees:
            balances = []
            for leave_type in types:
                if not leave_type.tracks_balance:
                    continue
                bal = leave_balance(db, employee.id, leave_type.id)
                balances.append({
                    "leave_type": leave_type.name_ar or leave_type.code,
                    "days": str(bal),
                })
            result.append({
                "code": employee.code,
                "name": employee.full_name,
                "balances": balances,
            })
        return {"employees": result, "note": "أرصدة محسوبة من سجل الحركات فقط (قراءة)."}


def format_leave_balance_ar(report: dict[str, Any]) -> str:
    lines = [report.get("note") or ""]
    if not report.get("employees"):
        return lines[0]
    for emp in report["employees"]:
        lines.append(f"الموظف {emp['code']} — {emp['name']}:")
        if not emp["balances"]:
            lines.append("   لا توجد أنواع إجازة تتابع رصيدًا.")
            continue
        for item in emp["balances"]:
            lines.append(f"   - {item['leave_type']}: {item['days']} يوم")
    return "\n".join(line for line in lines if line)


def build_advance_report(question: str) -> dict[str, Any]:
    with SessionLocal() as db:
        employees = _find_employees(db, question)
        query = select(AdvanceRequest, Employee).join(
            Employee, Employee.id == AdvanceRequest.employee_id
        ).where(AdvanceRequest.status.in_(["pending", "approved", "paid"]))
        if employees:
            ids = [item.id for item in employees]
            query = query.where(AdvanceRequest.employee_id.in_(ids))
        rows = db.execute(query.order_by(AdvanceRequest.id.desc()).limit(30)).all()
        items = []
        for request, employee in rows:
            installments = db.scalars(
                select(AdvanceInstallment).where(
                    AdvanceInstallment.advance_request_id == request.id
                ).order_by(AdvanceInstallment.installment_number)
            ).all()
            remaining = [
                inst for inst in installments
                if inst.status in {"scheduled", "reversed"}
            ]
            remaining_amount = sum((inst.amount for inst in remaining), Decimal("0"))
            items.append({
                "id": request.id,
                "code": employee.code,
                "name": employee.full_name,
                "amount": str(request.amount),
                "type": request.advance_type,
                "status": request.status,
                "remaining_installments": len(remaining),
                "remaining_amount": str(remaining_amount.quantize(Decimal("0.01"))),
            })
        return {
            "items": items,
            "note": "ملخص سلف نشطة/مدفوعة غير مغلقة بالكامل. قراءة فقط.",
        }


def format_advance_report_ar(report: dict[str, Any]) -> str:
    lines = [report.get("note") or ""]
    if not report.get("items"):
        lines.append("لا توجد سلف مطابقة للبحث.")
        return "\n".join(lines)
    for item in report["items"]:
        lines.append(
            f"#{item['id']} — {item['code']} {item['name']} — مبلغ {item['amount']} — "
            f"نوع {item['type']} — حالة {item['status']} — "
            f"أقساط متبقية {item['remaining_installments']} بقيمة {item['remaining_amount']}"
        )
    return "\n".join(lines)


def build_staff_actions_report(question: str) -> dict[str, Any]:
    month_start = _parse_month(question)
    month_end = _month_end(month_start)
    with SessionLocal() as db:
        employees = _find_employees(db, question)
        query = select(StaffAction, Employee).join(
            Employee, Employee.id == StaffAction.employee_id
        )
        query = query.where(
            or_(
                (StaffAction.payroll_month >= month_start) & (StaffAction.payroll_month <= month_end),
                (StaffAction.payroll_month.is_(None))
                & (func.date(StaffAction.created_at) >= month_start)
                & (func.date(StaffAction.created_at) <= month_end),
            )
        )
        if employees:
            query = query.where(StaffAction.employee_id.in_([e.id for e in employees]))
        rows = db.execute(query.order_by(StaffAction.id.desc()).limit(40)).all()
        items = []
        for action, employee in rows:
            items.append({
                "id": action.id,
                "code": employee.code,
                "name": employee.full_name,
                "kind": action.kind,
                "title": action.title,
                "status": action.status,
                "amount": str(action.amount) if action.amount is not None else None,
                "payroll_month": action.payroll_month.isoformat() if action.payroll_month else None,
            })
        return {
            "month": month_start.isoformat()[:7],
            "items": items,
            "note": "ملخص جزاءات/مكافآت للفترة. قراءة فقط ولم يُعتمد أي إجراء من هنا.",
        }


def format_staff_actions_ar(report: dict[str, Any]) -> str:
    lines = [
        f"جزاءات ومكافآت لشهر {report['month']}.",
        report.get("note") or "",
    ]
    if not report.get("items"):
        lines.append("لا توجد سجلات مطابقة.")
        return "\n".join(lines)
    for item in report["items"]:
        amount = f" — مبلغ {item['amount']}" if item["amount"] else ""
        lines.append(
            f"#{item['id']} — {item['code']} {item['name']} — {item['kind']} — "
            f"{item['title']} — {item['status']}{amount}"
        )
    return "\n".join(lines)


def answer_structured_hr_query(question: str) -> str:
    """Dispatch a natural-language HR question to the matching read-only report."""
    if is_attendance_analysis_request(question):
        q = question or ""
        if _DATE_RE.search(q) and "شهر" not in q:
            report = build_attendance_analysis(work_date=_parse_date(q), month_start=None)
        else:
            report = build_attendance_analysis(work_date=None, month_start=_parse_month(q))
        return format_attendance_analysis_ar(report)
    if is_leave_balance_request(question):
        return format_leave_balance_ar(build_leave_balance_report(question))
    if is_advance_query_request(question):
        return format_advance_report_ar(build_advance_report(question))
    if is_staff_action_query_request(question):
        return format_staff_actions_ar(build_staff_actions_report(question))
    return "لم أتعرّف على نوع الاستعلام. جرّب: تحليل حضور، رصيد إجازة، سلف، أو جزاءات."
