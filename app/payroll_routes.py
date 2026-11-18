"""Monthly payroll lifecycle routes."""
from datetime import date
from decimal import Decimal

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.payroll_service import (
    approve_payroll, close_payroll, create_payroll_period, record_payment,
    reopen_payroll, update_payroll_line,
)
from app.models import Employee, PayrollLine, PayrollPayment, PayrollPeriod
from app.security import csrf_token_for, verify_csrf_token


def register_payroll_routes(app, templates: Jinja2Templates, require_admin):
    def _payroll_context(db: Session, request: Request, error=None):
        periods = db.scalars(select(PayrollPeriod).order_by(PayrollPeriod.year.desc(), PayrollPeriod.month.desc())).all()
        selected_id = request.query_params.get("period", type=int)
        selected = db.get(PayrollPeriod, selected_id) if selected_id else (periods[0] if periods else None)
        lines = []
        if selected:
            lines = db.execute(
                select(PayrollLine, Employee.full_name, Employee.code)
                .join(Employee, Employee.id == PayrollLine.employee_id)
                .where(PayrollLine.payroll_period_id == selected.id)
                .order_by(PayrollLine.employee_code)
            ).all()
        return {"periods": periods, "selected": selected, "lines": lines, "error": error,
                "csrf_token": csrf_token_for(request)}

    def _payroll_redirect(request: Request, period_id=None, error=None):
        if error:
            request.session["payroll_error"] = error
        suffix = f"?period={period_id}" if period_id else ""
        return RedirectResponse("/payroll" + suffix, status_code=303)

    @app.get("/payroll", response_class=HTMLResponse)
    def payroll_page(request: Request):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        error = request.session.pop("payroll_error", None)
        with SessionLocal() as db:
            context = _payroll_context(db, request, error)
            return templates.TemplateResponse(request=request, name="payroll.html", context=context)

    @app.post("/payroll")
    def create_payroll_route(
        request: Request, year: int = Form(...), month: int = Form(...), csrf_token: str = Form(...),
    ):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return _payroll_redirect(request, error="انتهت صلاحية النموذج.")
        try:
            with SessionLocal() as db:
                period = create_payroll_period(db, year=year, month=month, actor=admin.username)
                db.commit()
                pid = period.id
        except ValueError as exc:
            msg = {"attendance_month_not_approved": "يجب اعتماد الحضور للشهر أولًا.",
                   "payroll_period_already_exists": "يوجد مسير لهذا الشهر بالفعل.",
                   "payroll_period_invalid": "السنة أو الشهر غير صالح."}.get(str(exc), "تعذر إنشاء المسير.")
            return _payroll_redirect(request, error=msg)
        return _payroll_redirect(request, pid)

    @app.post("/payroll/{period_id}/lines/{line_id}")
    def update_payroll_line_route(
        request: Request, period_id: int, line_id: int,
        attendance_bonus: Decimal = Form(0), performance_bonus: Decimal = Form(0),
        occupancy_bonus: Decimal = Form(0), overtime_hours: Decimal = Form(0),
        other_bonuses: Decimal = Form(0), absence_days: Decimal = Form(0),
        late_minutes: int = Form(0), early_minutes: int = Form(0),
        insurance_share: Decimal = Form(0), tax_withheld: Decimal = Form(0),
        other_deductions: Decimal = Form(0), advance_deduction: Decimal = Form(0),
        review_notes: str = Form(""), reason: str = Form(...), csrf_token: str = Form(...),
    ):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return _payroll_redirect(request, period_id, "انتهت صلاحية النموذج.")
        try:
            with SessionLocal() as db:
                row = db.get(PayrollLine, line_id)
                if row is None or row.payroll_period_id != period_id:
                    raise ValueError("payroll_line_not_found")
                update_payroll_line(db, line_id=line_id, values={
                    "attendance_bonus": attendance_bonus, "performance_bonus": performance_bonus,
                    "occupancy_bonus": occupancy_bonus, "overtime_hours": overtime_hours,
                    "other_bonuses": other_bonuses, "absence_days": absence_days,
                    "late_minutes": late_minutes, "early_minutes": early_minutes,
                    "insurance_share": insurance_share, "tax_withheld": tax_withheld,
                    "other_deductions": other_deductions, "advance_deduction": advance_deduction,
                    "review_notes": review_notes.strip() or None,
                }, reason=reason, actor=admin.username)
                db.commit()
        except ValueError:
            return _payroll_redirect(request, period_id, "تعذر تعديل البند؛ راجع القيم وسبب التعديل وحالة المسير.")
        return _payroll_redirect(request, period_id)

    @app.post("/payroll/{period_id}/approve")
    def approve_payroll_route(request: Request, period_id: int, reason: str = Form(...), csrf_token: str = Form(...)):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return _payroll_redirect(request, period_id, "انتهت صلاحية النموذج.")
        try:
            with SessionLocal() as db:
                approve_payroll(db, period_id=period_id, reason=reason, actor=admin.username)
                db.commit()
        except ValueError:
            return _payroll_redirect(request, period_id, "تعذر اعتماد المسير؛ يلزم سبب وحالة مسودة.")
        return _payroll_redirect(request, period_id)

    @app.post("/payroll/{period_id}/close")
    def close_payroll_route(request: Request, period_id: int, csrf_token: str = Form(...)):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return _payroll_redirect(request, period_id, "انتهت صلاحية النموذج.")
        try:
            with SessionLocal() as db:
                close_payroll(db, period_id=period_id, actor=admin.username)
                db.commit()
        except ValueError:
            return _payroll_redirect(request, period_id, "يجب اعتماد المسير قبل إغلاقه.")
        return _payroll_redirect(request, period_id)

    @app.post("/payroll/{period_id}/reopen")
    def reopen_payroll_route(request: Request, period_id: int, reason: str = Form(...), csrf_token: str = Form(...)):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return _payroll_redirect(request, period_id, "انتهت صلاحية النموذج.")
        try:
            with SessionLocal() as db:
                reopen_payroll(db, period_id=period_id, reason=reason, actor=admin.username)
                db.commit()
        except ValueError:
            return _payroll_redirect(request, period_id, "تعذر إعادة فتح المسير؛ يلزم سبب وحالة مغلقة.")
        return _payroll_redirect(request, period_id)

    @app.post("/payroll/{period_id}/lines/{line_id}/payments")
    def payroll_payment_route(
        request: Request, period_id: int, line_id: int, amount: Decimal = Form(...),
        paid_on: date = Form(...), method: str = Form(...), reference: str = Form(""),
        csrf_token: str = Form(...),
    ):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return _payroll_redirect(request, period_id, "انتهت صلاحية النموذج.")
        try:
            with SessionLocal() as db:
                row = db.get(PayrollLine, line_id)
                if row is None or row.payroll_period_id != period_id:
                    raise ValueError("payroll_line_not_found")
                record_payment(db, line_id=line_id, amount=amount, paid_on=paid_on, method=method,
                               reference=reference, actor=admin.username)
                db.commit()
        except ValueError:
            return _payroll_redirect(request, period_id, "تعذر تسجيل الصرف؛ راجع المبلغ والطريقة واعتماد المسير.")
        return _payroll_redirect(request, period_id)
