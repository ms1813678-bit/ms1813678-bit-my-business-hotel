"""Administrator routes for salary advances and staff actions."""
from datetime import date
from decimal import Decimal

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Employee, SalaryAdvance, StaffAction
from app.security import csrf_token_for, verify_csrf_token
from app.staff_finance_service import (
    cancel_salary_advance,
    cancel_staff_action,
    create_salary_advance,
    create_staff_action,
)


def register_staff_finance_routes(app, templates: Jinja2Templates, require_admin):
    def _context(db, request, *, error: str | None = None):
        employees = db.scalars(
            select(Employee).where(Employee.status == "active").order_by(Employee.code)
        ).all()
        advances = db.scalars(
            select(SalaryAdvance).order_by(SalaryAdvance.request_date.desc()).limit(100)
        ).all()
        actions = db.scalars(
            select(StaffAction).order_by(StaffAction.action_date.desc()).limit(100)
        ).all()
        return {
            "app_name": settings.app_name,
            "employees": employees,
            "advances": advances,
            "actions": actions,
            "error": error,
            "csrf_token": csrf_token_for(request),
        }

    def _redirect(error: str | None = None):
        if error:
            return RedirectResponse(f"/staff-finance?error={error}", status_code=303)
        return RedirectResponse("/staff-finance", status_code=303)

    @app.get("/staff-finance", response_class=HTMLResponse)
    def staff_finance_page(request: Request):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        error = request.query_params.get("error")
        with SessionLocal() as db:
            return templates.TemplateResponse(
                request=request,
                name="staff_finance.html",
                context=_context(db, request, error=error),
            )

    @app.post("/staff-finance/advances")
    def create_advance_route(
        request: Request,
        employee_id: int = Form(...),
        request_date: date = Form(...),
        amount: Decimal = Form(...),
        reason: str = Form(...),
        csrf_token: str = Form(...),
    ):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return _redirect("انتهت صلاحية النموذج.")
        try:
            with SessionLocal() as db:
                create_salary_advance(
                    db,
                    employee_id=employee_id,
                    request_date=request_date,
                    amount=amount,
                    reason=reason,
                    actor=admin.username,
                )
                db.commit()
        except ValueError as exc:
            msg = {
                "advance_reason_required": "سبب السلفة مطلوب.",
                "advance_amount_invalid": "مبلغ السلفة غير صالح.",
                "advance_outside_request_window": "نافذة طلب السلفة من 15 إلى 22 من الشهر فقط.",
                "advance_employee_not_active": "الموظف غير نشط.",
                "advance_exceeds_outstanding_limit": "تجاوز حد السلف القائمة (25٪ من الراتب الأساسي).",
            }.get(str(exc), "تعذر تسجيل السلفة.")
            return _redirect(msg)
        return _redirect()

    @app.post("/staff-finance/advances/{advance_id}/cancel")
    def cancel_advance_route(
        request: Request,
        advance_id: int,
        reason: str = Form(...),
        csrf_token: str = Form(...),
    ):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return _redirect("انتهت صلاحية النموذج.")
        try:
            with SessionLocal() as db:
                cancel_salary_advance(
                    db, advance_id=advance_id, reason=reason, actor=admin.username
                )
                db.commit()
        except ValueError:
            return _redirect("تعذر إلغاء السلفة.")
        return _redirect()

    @app.post("/staff-finance/actions")
    def create_action_route(
        request: Request,
        employee_id: int = Form(...),
        action_date: date = Form(...),
        action_kind: str = Form(...),
        category: str = Form(...),
        amount: Decimal = Form(0),
        reason: str = Form(...),
        csrf_token: str = Form(...),
    ):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return _redirect("انتهت صلاحية النموذج.")
        try:
            with SessionLocal() as db:
                create_staff_action(
                    db,
                    employee_id=employee_id,
                    action_date=action_date,
                    action_kind=action_kind,
                    category=category,
                    amount=amount if amount else None,
                    reason=reason,
                    actor=admin.username,
                )
                db.commit()
        except ValueError as exc:
            return _redirect(f"تعذر تسجيل الإجراء: {exc}")
        return _redirect()

    @app.post("/staff-finance/actions/{action_id}/cancel")
    def cancel_action_route(
        request: Request,
        action_id: int,
        reason: str = Form(...),
        csrf_token: str = Form(...),
    ):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return _redirect("انتهت صلاحية النموذج.")
        try:
            with SessionLocal() as db:
                cancel_staff_action(
                    db, action_id=action_id, reason=reason, actor=admin.username
                )
                db.commit()
        except ValueError:
            return _redirect("تعذر إلغاء الإجراء.")
        return _redirect()
