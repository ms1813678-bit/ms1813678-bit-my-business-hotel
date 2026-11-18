"""Leave types, balances, and records routes."""
from datetime import date
from decimal import Decimal

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.leave_service import cancel_leave, confirm_leave, create_leave_type, set_manual_balance
from app.models import Employee, EmployeeLeaveBalance, LeaveRecord, LeaveType
from app.security import csrf_token_for, verify_csrf_token


def register_leave_routes(app, templates: Jinja2Templates, require_admin):
    def _leave_page_context(db: Session, request: Request, *, error: str | None = None):
        employees = db.scalars(
            select(Employee).where(Employee.status == "active").order_by(Employee.code)
        ).all()
        leave_types = db.scalars(
            select(LeaveType).where(LeaveType.active.is_(True)).order_by(LeaveType.name)
        ).all()
        balances = db.execute(
            select(EmployeeLeaveBalance, Employee.full_name, Employee.code, LeaveType.name)
            .join(Employee, Employee.id == EmployeeLeaveBalance.employee_id)
            .join(LeaveType, LeaveType.id == EmployeeLeaveBalance.leave_type_id)
            .order_by(Employee.code, LeaveType.name)
        ).all()
        records = db.execute(
            select(LeaveRecord, Employee.full_name, Employee.code, LeaveType.name)
            .join(Employee, Employee.id == LeaveRecord.employee_id)
            .join(LeaveType, LeaveType.id == LeaveRecord.leave_type_id)
            .order_by(LeaveRecord.start_date.desc(), LeaveRecord.id.desc())
        ).all()
        return {
            "employees": employees, "leave_types": leave_types, "balances": balances,
            "records": records, "error": error, "csrf_token": csrf_token_for(request),
        }

    def _leave_redirect(request: Request, error: str | None = None):
        if error:
            request.session["leave_error"] = error
        return RedirectResponse("/leave", status_code=303)

    @app.get("/leave", response_class=HTMLResponse)
    def leave_page(request: Request):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        error = request.session.pop("leave_error", None)
        with SessionLocal() as db:
            return templates.TemplateResponse(
                request=request, name="leave.html",
                context=_leave_page_context(db, request, error=error),
            )

    @app.post("/leave/types")
    def add_leave_type(
        request: Request, code: str = Form(...), name: str = Form(...),
        paid: bool = Form(False), deducts_balance: bool = Form(False),
        csrf_token: str = Form(...),
    ):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return _leave_redirect(request, "انتهت صلاحية النموذج.")
        try:
            with SessionLocal() as db:
                create_leave_type(
                    db, code=code, name=name, paid=paid, deducts_balance=deducts_balance,
                )
                db.commit()
        except ValueError:
            return _leave_redirect(request, "رمز ونوع الإجازة مطلوبان.")
        except IntegrityError:
            return _leave_redirect(request, "رمز نوع الإجازة مستخدم بالفعل.")
        return _leave_redirect(request)

    @app.post("/leave/balances")
    def update_leave_balance(
        request: Request, employee_id: int = Form(...), leave_type_id: int = Form(...),
        balance: Decimal = Form(...), reason: str = Form(...), csrf_token: str = Form(...),
    ):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return _leave_redirect(request, "انتهت صلاحية النموذج.")
        try:
            with SessionLocal() as db:
                set_manual_balance(
                    db, employee_id=employee_id, leave_type_id=leave_type_id,
                    balance=balance, reason=reason, actor=admin.username,
                )
                db.commit()
        except ValueError:
            return _leave_redirect(request, "تعذر حفظ الرصيد؛ راجع الموظف والنوع والسبب.")
        return _leave_redirect(request)

    @app.post("/leave/records")
    def add_leave_record(
        request: Request, employee_id: int = Form(...), leave_type_id: int = Form(...),
        start_date: date = Form(...), end_date: date = Form(...), days: Decimal = Form(...),
        reason: str = Form(...), csrf_token: str = Form(...),
    ):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return _leave_redirect(request, "انتهت صلاحية النموذج.")
        try:
            with SessionLocal() as db:
                confirm_leave(
                    db, employee_id=employee_id, leave_type_id=leave_type_id,
                    start_date=start_date, end_date=end_date, days=days,
                    reason=reason, actor=admin.username,
                )
                db.commit()
        except ValueError as exc:
            messages = {
                "leave_balance_insufficient": "الرصيد غير كافٍ أو لم يتم إدخاله يدويًا.",
                "leave_dates_overlap_existing_record": "تتداخل الإجازة مع سجل مؤكد آخر.",
                "leave_dates_or_days_invalid": "راجع تاريخ البداية والنهاية وعدد الأيام.",
                "leave_before_employment": "الإجازة تسبق تاريخ التعيين.",
            }
            return _leave_redirect(request, messages.get(str(exc), "تعذر تسجيل الإجازة؛ راجع البيانات والسبب."))
        return _leave_redirect(request)

    @app.post("/leave/records/{leave_record_id}/cancel")
    def cancel_leave_record(
        request: Request, leave_record_id: int, reason: str = Form(...),
        csrf_token: str = Form(...),
    ):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return _leave_redirect(request, "انتهت صلاحية النموذج.")
        try:
            with SessionLocal() as db:
                cancel_leave(
                    db, leave_record_id=leave_record_id, reason=reason, actor=admin.username,
                )
                db.commit()
        except ValueError:
            return _leave_redirect(request, "تعذر إلغاء السجل؛ قد يكون ملغيًا بالفعل أو السبب فارغًا.")
        return _leave_redirect(request)
