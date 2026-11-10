"""Safe local command agent for the HR assistant.

The language model may propose an action, but it never gets direct database, shell,
filesystem, or network access. Only this fixed allowlist of business operations
can mutate the database, and every operation uses existing workflow rules.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

from app.ai_assistant import ask_local_model_json, local_model_status
from app.employee_codes import allocate_employee_code
from app.advance_workflow import approve_advance, create_advance, mark_advance_paid, reject_advance
from app.db import SessionLocal
from app.leave_workflow import add_balance_entry, cancel_leave, confirm_leave
from app.models import (
    AdvanceRequest,
    AttendanceDailyRecord,
    AttendanceMonth,
    Department,
    Branch,
    EmployeeChange,
    EmployeeServicePeriod,
    Employee,
    LeaveRecord,
    LeaveType,
    PayrollRun,
    StaffAction,
    AuditLog,
)
from app.attendance_workflow import approve_attendance_day, approve_attendance_month
from app.payroll_workflow import approve_payroll_run, close_payroll_run, reopen_payroll_run
from app.staff_actions_workflow import approve_staff_action, cancel_staff_action


WRITE_PREFIXES = ("نفذ ", "نفّذ ", "نفذ:", "نفّذ:")
COMMAND_WORDS = (
    "أنشئ", "انشئ", "أضف", "اضف", "عدّل", "عدل", "غيّر", "غير", "انقل",
    "أرشف", "ارشف", "أعد تعيين", "اعد تعيين", "سجّل", "سجل", "اعتمد",
    "وافق", "ألغ", "الغ", "ادفع", "أغلق", "اغلق", "أعد فتح", "اعد فتح",
)

ALLOWED_ACTIONS = {
    "create_employee",
    "edit_employee",
    "transfer_employee",
    "archive_employee",
    "rehire_employee",
    "create_leave",
    "confirm_leave",
    "cancel_leave",
    "adjust_leave_balance",
    "create_advance",
    "approve_advance",
    "reject_advance",
    "mark_advance_paid",
    "create_staff_action",
    "approve_staff_action",
    "cancel_staff_action",
    "approve_attendance_day",
    "approve_attendance_month",
    "approve_payroll_run",
    "close_payroll_run",
    "reopen_payroll_run",
}


def is_command_request(question: str) -> bool:
    q = " ".join(question.strip().split())
    return q.startswith(WRITE_PREFIXES) or any(q.startswith(word) for word in COMMAND_WORDS)


def wants_execution(question: str) -> bool:
    q = " ".join(question.strip().split())
    return q.startswith(WRITE_PREFIXES)


def _loopback_only(url: str) -> bool:
    host = urlparse(url).hostname
    return host in {"127.0.0.1", "localhost", "::1"}


def _parse_date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"التاريخ {field} غير صالح؛ استخدم YYYY-MM-DD.") from exc


def _decimal(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"القيمة {field} غير رقمية.") from exc


def _plan_context(db) -> dict[str, Any]:
    employees = db.scalars(select(Employee).order_by(Employee.code)).all()
    departments = db.scalars(select(Department).where(Department.active.is_(True)).order_by(Department.name)).all()
    leave_types = db.scalars(select(LeaveType).where(LeaveType.active.is_(True)).order_by(LeaveType.id)).all()
    return {
        "employees": [{"id": e.id, "code": e.code, "name": e.full_name, "status": e.status} for e in employees],
        "departments": [{"id": d.id, "name": d.name} for d in departments],
        "leave_types": [{"id": t.id, "code": t.code, "name": t.name_ar} for t in leave_types],
    }


def _json_plan(question: str, context: dict[str, Any]) -> dict[str, Any]:
    status = local_model_status()
    if not status["available"]:
        raise RuntimeError(status["message"])
    prompt = (
        "حوّل أمر مسؤول الموارد البشرية إلى JSON فقط، بدون Markdown. "
        "لا تخترع IDs؛ استخدم فقط IDs الموجودة في البيانات. "
        "action يجب أن يكون واحدًا من القائمة: " + ", ".join(sorted(ALLOWED_ACTIONS)) + ". "
        "استخدم null للقيمة غير المطلوبة. الحقول المسموحة: action, employee_id, leave_type_id, "
        "record_id, balance_days, transaction_type, advance_id, amount, advance_type, "
        "installment_count, staff_action_id, kind, category, title, is_financial, payroll_month, "
        "attendance_record_id, clock_in, clock_out, month_id, payroll_run_id, reason, "
        "start_date, end_date, charged_days, full_name, national_id, phone, address, position, "
        "department_id, branch_id, hire_date, basic_salary, daily_hours, fingerprint_id, effective_date. "
        "أعد أيضًا summary بالعربية قصيرة. "
        f"الأمر: {question}\nالبيانات المحلية: {json.dumps(context, ensure_ascii=False)}"
    )
    from app.ai_assistant import _post_json
    response = _post_json(
        "/api/chat",
        {
            "model": status["model"],
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "أنت محلل أوامر لنظام HR محلي. لا تنفذ أي شيء بنفسك. "
                        "أخرج JSON صالحًا فقط. لا تقترح SQL أو shell أو HTTP أو ملفات."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        },
        timeout=30,
    )
    raw = str(response.get("message", {}).get("content", "")).strip()
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("النموذج المحلي لم يُرجع أمرًا بصيغة JSON صحيحة.") from exc
    if not isinstance(plan, dict) or plan.get("action") not in ALLOWED_ACTIONS:
        raise ValueError("الأمر المطلوب غير مسموح به في المساعد.")
    return plan


def build_plan(question: str) -> dict[str, Any]:
    with SessionLocal() as db:
        return _json_plan(question, _plan_context(db))


def execute_plan(plan: dict[str, Any], actor: str) -> str:
    action = plan.get("action")
    reason = str(plan.get("reason") or "").strip() or "تنفيذ بأمر صريح من مسؤول الموارد البشرية عبر المساعد المحلي."
    if action not in ALLOWED_ACTIONS:
        raise ValueError("الأمر غير مسموح.")

    with SessionLocal.begin() as db:
        if action == "create_employee":
            full_name = str(plan.get("full_name") or "").strip()
            position = str(plan.get("position") or "").strip()
            department_id = int(plan["department_id"])
            branch_id = int(plan["branch_id"]) if plan.get("branch_id") is not None else None
            hire_date = _parse_date(plan.get("hire_date") or date.today().isoformat(), "التعيين")
            basic_salary = _decimal(plan.get("basic_salary"), "الراتب")
            daily_hours = _decimal(plan.get("daily_hours") or 8, "ساعات العمل")
            if not full_name or not position or basic_salary < 0 or not (0 < daily_hours <= 24):
                raise ValueError("بيانات الموظف الأساسية غير صحيحة.")
            department = db.get(Department, department_id)
            if department is None or not department.active:
                raise ValueError("القسم غير متاح.")
            branch = db.get(Branch, branch_id) if branch_id is not None else None
            if branch_id is not None and (branch is None or not branch.active or branch.department_id != department_id):
                raise ValueError("الفرع غير تابع للقسم.")
            national_id = str(plan.get("national_id") or "").strip() or None
            fingerprint_id = str(plan.get("fingerprint_id") or "").strip() or None
            for field, value in (("national_id", national_id), ("fingerprint_id", fingerprint_id)):
                if value and db.scalar(select(Employee.id).where(getattr(Employee, field) == value).limit(1)):
                    raise ValueError(f"{field} مستخدم بالفعل.")
            employee = Employee(code=allocate_employee_code(db), full_name=full_name,
                national_id=national_id, phone=str(plan.get("phone") or "").strip() or None,
                address=str(plan.get("address") or "").strip() or None,
                department_id=department_id, branch_id=branch_id, position=position,
                hire_date=hire_date, basic_salary=basic_salary, daily_hours=daily_hours,
                fingerprint_id=fingerprint_id, status="active")
            db.add(employee); db.flush()
            db.add(EmployeeServicePeriod(employee_id=employee.id, start_date=hire_date, start_reason=reason))
            db.add(AuditLog(actor=actor, action="employee_created_by_ai", entity_type="employee",
                entity_id=str(employee.id), new_value=f"code={employee.code}; name={employee.full_name}", reason=reason))
            return f"تم إنشاء الموظف {employee.full_name} برقم {employee.id} وكود {employee.code}."

        if action == "edit_employee":
            employee = db.get(Employee, int(plan["employee_id"]))
            if employee is None: raise ValueError("الموظف غير موجود.")
            fields = ("full_name", "position", "department_id", "branch_id", "basic_salary", "daily_hours",
                      "phone", "address", "national_id", "fingerprint_id")
            if "full_name" in plan and not str(plan["full_name"]).strip():
                raise ValueError("اسم الموظف لا يمكن أن يكون فارغًا.")
            if "position" in plan and not str(plan["position"]).strip():
                raise ValueError("المسمى الوظيفي لا يمكن أن يكون فارغًا.")
            if "department_id" in plan or "branch_id" in plan:
                department_id = int(plan.get("department_id", employee.department_id))
                branch_id = int(plan["branch_id"]) if plan.get("branch_id") is not None else employee.branch_id
                department = db.get(Department, department_id)
                if department is None or not department.active:
                    raise ValueError("القسم المحدد غير متاح.")
                if branch_id is not None:
                    branch = db.get(Branch, branch_id)
                    if branch is None or not branch.active or branch.department_id != department_id:
                        raise ValueError("الفرع المحدد غير تابع للقسم.")
            for field in fields:
                if field in plan and plan[field] is not None:
                    new_value = plan[field]
                    if field in {"department_id", "branch_id"}:
                        new_value = int(new_value)
                    if field in {"basic_salary", "daily_hours"}:
                        new_value = _decimal(new_value, field)
                    if field in {"basic_salary", "daily_hours"} and (
                        new_value < 0 or (field == "daily_hours" and not (0 < new_value <= 24))
                    ):
                        raise ValueError("بيانات الراتب أو ساعات العمل غير صحيحة.")
                    old_value = getattr(employee, field)
                    if old_value != new_value:
                        setattr(employee, field, new_value)
                        db.add(EmployeeChange(employee_id=employee.id, field_name=field,
                            old_value=str(old_value) if old_value is not None else None,
                            new_value=str(new_value) if new_value is not None else None,
                            effective_date=_parse_date(plan.get("effective_date") or date.today().isoformat(), "السريان"),
                            reason=reason))
            db.add(AuditLog(actor=actor, action="employee_updated_by_ai", entity_type="employee",
                entity_id=str(employee.id), reason=reason))
            return f"تم تعديل بيانات الموظف {employee.full_name}."

        if action == "transfer_employee":
            employee = db.get(Employee, int(plan["employee_id"]))
            if employee is None or employee.status != "active": raise ValueError("الموظف غير موجود أو غير نشط.")
            department_id = int(plan["department_id"])
            branch_id = int(plan["branch_id"]) if plan.get("branch_id") is not None else None
            department = db.get(Department, department_id)
            branch = db.get(Branch, branch_id) if branch_id is not None else None
            if department is None or not department.active: raise ValueError("القسم الجديد غير متاح.")
            if branch_id is not None and (branch is None or not branch.active or branch.department_id != department_id):
                raise ValueError("الفرع الجديد غير صالح.")
            effective = _parse_date(plan.get("effective_date") or date.today().isoformat(), "السريان")
            old_department, old_branch = employee.department_id, employee.branch_id
            employee.department_id, employee.branch_id = department_id, branch_id
            for field, old_value, new_value in (("department_id", old_department, department_id), ("branch_id", old_branch, branch_id)):
                if old_value != new_value:
                    db.add(EmployeeChange(employee_id=employee.id, field_name=field,
                        old_value=str(old_value) if old_value is not None else None,
                        new_value=str(new_value) if new_value is not None else None,
                        effective_date=effective, reason=reason))
            db.add(AuditLog(actor=actor, action="employee_transferred_by_ai", entity_type="employee",
                entity_id=str(employee.id), reason=reason))
            return f"تم نقل الموظف {employee.full_name}."

        if action == "archive_employee":
            employee = db.get(Employee, int(plan["employee_id"]))
            if employee is None or employee.status != "active": raise ValueError("الموظف غير موجود أو غير نشط.")
            period = db.scalar(select(EmployeeServicePeriod).where(
                EmployeeServicePeriod.employee_id == employee.id, EmployeeServicePeriod.end_date.is_(None)))
            if period is None: raise ValueError("لا توجد فترة خدمة مفتوحة.")
            period.end_date = date.today(); period.end_reason = reason; employee.status = "archived"
            db.add(AuditLog(actor=actor, action="employee_archived_by_ai", entity_type="employee",
                entity_id=str(employee.id), old_value="active", new_value="archived", reason=reason))
            return f"تمت أرشفة الموظف {employee.full_name}."

        if action == "rehire_employee":
            employee = db.get(Employee, int(plan["employee_id"]))
            if employee is None or employee.status != "archived": raise ValueError("الموظف غير مؤرشف.")
            new_hire = _parse_date(plan.get("hire_date") or date.today().isoformat(), "إعادة التعيين")
            previous = db.scalar(select(EmployeeServicePeriod).where(
                EmployeeServicePeriod.employee_id == employee.id).order_by(EmployeeServicePeriod.id.desc()).limit(1))
            if previous is None or previous.end_date is None or new_hire <= previous.end_date:
                raise ValueError("تاريخ إعادة التعيين غير صالح.")
            employee.status = "active"; employee.hire_date = new_hire
            db.add(EmployeeServicePeriod(employee_id=employee.id, start_date=new_hire, start_reason=reason))
            db.add(AuditLog(actor=actor, action="employee_rehired_by_ai", entity_type="employee",
                entity_id=str(employee.id), old_value="archived", new_value="active", reason=reason))
            return f"تمت إعادة تعيين الموظف {employee.full_name}."

        if action == "create_leave":
            employee = db.get(Employee, int(plan["employee_id"]))
            leave_type = db.get(LeaveType, int(plan["leave_type_id"]))
            start = _parse_date(plan["start_date"], "البداية")
            end = _parse_date(plan["end_date"], "النهاية")
            charged = _decimal(plan["charged_days"], "الأيام")
            if employee is None or employee.status != "active" or leave_type is None or not leave_type.active:
                raise ValueError("الموظف أو نوع الإجازة غير صالح.")
            if end < start or charged <= 0 or charged > Decimal((end - start).days + 1):
                raise ValueError("بيانات الإجازة غير صحيحة.")
            overlap = db.scalar(select(LeaveRecord.id).where(
                LeaveRecord.employee_id == employee.id,
                LeaveRecord.status != "cancelled",
                LeaveRecord.start_date <= end,
                LeaveRecord.end_date >= start,
            ).limit(1))
            if overlap is not None:
                raise ValueError("يوجد تداخل مع إجازة أخرى.")
            record = LeaveRecord(
                employee_id=employee.id, leave_type_id=leave_type.id,
                start_date=start, end_date=end, calendar_days=(end-start).days+1,
                charged_days=charged, reason=reason, status="draft", created_by=actor,
            )
            db.add(record); db.flush()
            db.add(AuditLog(actor=actor, action="ai_leave_record_created", entity_type="leave_record",
                            entity_id=str(record.id), new_value=f"{start}|{end}|{charged}", reason=reason))
            return f"تم إنشاء مسودة إجازة رقم {record.id} للموظف {employee.full_name}."

        if action == "confirm_leave":
            record = db.scalar(select(LeaveRecord).where(LeaveRecord.id == int(plan["record_id"])).with_for_update())
            if record is None: raise ValueError("سجل الإجازة غير موجود.")
            confirm_leave(db, record, actor)
            return f"تم اعتماد الإجازة رقم {record.id}."

        if action == "cancel_leave":
            record = db.scalar(select(LeaveRecord).where(LeaveRecord.id == int(plan["record_id"])).with_for_update())
            if record is None: raise ValueError("سجل الإجازة غير موجود.")
            cancel_leave(db, record, actor, reason)
            return f"تم إلغاء الإجازة رقم {record.id}."

        if action == "adjust_leave_balance":
            employee = db.get(Employee, int(plan["employee_id"]))
            leave_type = db.get(LeaveType, int(plan["leave_type_id"]))
            days = _decimal(plan["balance_days"], "الرصيد")
            if employee is None or leave_type is None: raise ValueError("الموظف أو نوع الإجازة غير موجود.")
            add_balance_entry(db, employee_id=employee.id, leave_type=leave_type,
                              transaction_type=str(plan.get("transaction_type") or "adjustment"),
                              days_delta=days, reason=reason, actor=actor)
            return f"تم تعديل رصيد {leave_type.name_ar} للموظف {employee.full_name} بمقدار {days}."

        if action in {"create_advance", "approve_advance", "reject_advance", "mark_advance_paid"}:
            req = db.get(AdvanceRequest, int(plan.get("advance_id") or 0))
            if action == "create_advance":
                request_date = _parse_date(plan.get("request_date") or date.today().isoformat(), "الطلب")
                req = create_advance(db, employee_id=int(plan["employee_id"]), request_date=request_date,
                                     amount=_decimal(plan["amount"], "المبلغ"), reason=reason,
                                     advance_type=str(plan.get("advance_type") or "regular"), actor=actor)
                return f"تم إنشاء طلب سلفة رقم {req.id}."
            if req is None: raise ValueError("طلب السلفة غير موجود.")
            if action == "approve_advance":
                approve_advance(db, req, actor, int(plan.get("installment_count") or 1))
                return f"تم اعتماد السلفة رقم {req.id}."
            if action == "reject_advance":
                reject_advance(db, req, actor, reason)
                return f"تم رفض السلفة رقم {req.id}."
            mark_advance_paid(db, req, actor)
            return f"تم تسجيل سداد السلفة رقم {req.id}."

        if action in {"create_staff_action", "approve_staff_action", "cancel_staff_action"}:
            if action == "create_staff_action":
                from app.staff_actions_workflow import create_staff_action
                item = create_staff_action(
                    db, employee_id=int(plan["employee_id"]), kind=str(plan["kind"]),
                    category=str(plan["category"]), title=str(plan["title"]),
                    is_financial=bool(plan.get("is_financial")),
                    amount=_decimal(plan["amount"], "المبلغ") if plan.get("amount") is not None else None,
                    payroll_month=_parse_date(plan["payroll_month"], "شهر الرواتب") if plan.get("payroll_month") else None,
                    reason=reason, actor=actor)
                return f"تم إنشاء إجراء موظف رقم {item.id}."
            item = db.get(StaffAction, int(plan.get("staff_action_id") or 0))
            if item is None: raise ValueError("إجراء الموظف غير موجود.")
            if action == "approve_staff_action": approve_staff_action(db, item, actor); return f"تم اعتماد إجراء الموظف رقم {item.id}."
            cancel_staff_action(db, item, actor, reason); return f"تم إلغاء إجراء الموظف رقم {item.id}."

        if action == "approve_attendance_day":
            record = db.get(AttendanceDailyRecord, int(plan["attendance_record_id"]))
            if record is None: raise ValueError("سجل الحضور غير موجود.")
            approve_attendance_day(db, record_id=record.id,
                clock_in=__import__("datetime").datetime.fromisoformat(str(plan["clock_in"])),
                clock_out=__import__("datetime").datetime.fromisoformat(str(plan["clock_out"])),
                actor=actor, reason=reason)
            return f"تم اعتماد سجل الحضور رقم {record.id}."

        if action == "approve_attendance_month":
            approve_attendance_month(db, month_id=int(plan["month_id"]), actor=actor,
                                     reason=reason, coverage_confirmed=bool(plan.get("coverage_confirmed")))
            return f"تم اعتماد شهر الحضور رقم {plan['month_id']}."

        if action in {"approve_payroll_run", "close_payroll_run", "reopen_payroll_run"}:
            run = db.scalar(select(PayrollRun).where(PayrollRun.id == int(plan["payroll_run_id"])).with_for_update())
            if run is None: raise ValueError("مسير الرواتب غير موجود.")
            if action == "approve_payroll_run": approve_payroll_run(db, run, actor); return f"تم اعتماد مسير الرواتب رقم {run.id}."
            if action == "close_payroll_run": close_payroll_run(db, run, actor); return f"تم إغلاق مسير الرواتب رقم {run.id}."
            reopen_payroll_run(db, run, actor, reason); return f"تم إعادة فتح مسير الرواتب رقم {run.id}."

    raise ValueError("لم يتم تنفيذ الأمر.")


def run_ai_command(question: str, actor: str) -> dict[str, Any]:
    if not _loopback_only(__import__("app.ai_assistant", fromlist=["OLLAMA_BASE_URL"]).OLLAMA_BASE_URL):
        raise RuntimeError("تم منع الاتصال لأن عنوان نموذج الذكاء المحلي ليس على الجهاز نفسه.")
    plan = build_plan(question)
    executing = wants_execution(question)
    summary = str(plan.get("summary") or "تم تحليل الأمر.")
    if not executing:
        return {"executed": False, "plan": plan, "message": f"معاينة فقط: {summary} اكتب «نفذ: {question}» لتنفيذ الأمر."}
    message = execute_plan(plan, actor)
    return {"executed": True, "plan": plan, "message": message}
