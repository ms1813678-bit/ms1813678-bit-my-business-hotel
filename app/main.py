"""Local HR application routes for administrator access and employee registration."""
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.auth import authenticate, get_session_admin
from app.config import settings
from app.db import SessionLocal, engine
from app.employee_codes import reserve_employee_code
from app.employee_lifecycle import archive_employee, rehire_employee, update_employee_profile
from app.leave_service import cancel_leave, confirm_leave, create_leave_type, set_manual_balance
from app.payroll_service import (approve_payroll, close_payroll, create_payroll_period, record_payment, reopen_payroll, update_payroll_line)
from app.models import (
    AdminAccount, AuditLog, Branch, Department, Employee, EmployeeChange, EmployeeServicePeriod,
    EmployeeLeaveBalance, LeaveRecord, LeaveType, PayrollLine, PayrollPayment, PayrollPeriod,
)
from app.security import csrf_token_for, rotate_csrf_token, verify_csrf_token
from app.reports_routes import register_reports_routes
from app.backup_routes import register_backup_routes
from app.ai_routes import register_ai_routes
from app.staff_finance_routes import register_staff_finance_routes


PROJECT_ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(
    title=settings.app_name,
    docs_url=None if settings.app_env == "production" else "/docs",
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    max_age=8 * 60 * 60,
    same_site="strict",
    https_only=False,
)
app.mount(
    "/static",
    StaticFiles(directory=str(PROJECT_ROOT / "app" / "static")),
    name="static",
)
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "app" / "templates"))


@app.get("/health")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except SQLAlchemyError:
        return {"status": "degraded", "database": "unavailable"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None, "csrf_token": csrf_token_for(request)},
    )


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    if not verify_csrf_token(request, csrf_token):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": "انتهت صلاحية النموذج. حدّث الصفحة وحاول مرة أخرى.",
                "csrf_token": csrf_token_for(request),
            },
            status_code=403,
        )

    with SessionLocal() as db:
        admin = authenticate(db, username.strip(), password)
        if admin is None:
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "error": "اسم المستخدم أو كلمة المرور غير صحيحة.",
                    "csrf_token": csrf_token_for(request),
                },
                status_code=401,
            )
        admin_id = admin.id
        session_version = admin.session_version
        admin_username = admin.username

    request.session.clear()
    request.session.update({
        "admin_id": admin_id,
        "admin_username": admin_username,
        "session_version": session_version,
    })
    rotate_csrf_token(request)
    return RedirectResponse("/", status_code=303)


def require_admin(request: Request) -> AdminAccount | RedirectResponse:
    admin_id = request.session.get("admin_id")
    session_version = request.session.get("session_version")
    if not isinstance(admin_id, int) or not isinstance(session_version, int):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    with SessionLocal() as db:
        admin = get_session_admin(
            db, admin_id=admin_id, session_version=session_version
        )
        if admin is None:
            request.session.clear()
            return RedirectResponse("/login", status_code=303)
        return admin


def _employee_page_context(db: Session, request: Request, *, error: str | None = None):
    departments = db.scalars(
        select(Department)
        .where(Department.active.is_(True))
        .order_by(Department.name)
    ).all()
    branches = db.scalars(
        select(Branch).where(Branch.active.is_(True)).order_by(Branch.name)
    ).all()
    employees = db.scalars(
        select(Employee).order_by(Employee.code)
    ).all()
    return {
        "departments": departments,
        "branches": branches,
        "employees": employees,
        "error": error,
        "csrf_token": csrf_token_for(request),
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    admin = require_admin(request)
    if isinstance(admin, RedirectResponse):
        return admin
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "app_name": settings.app_name,
            "admin": admin.username,
            "csrf_token": csrf_token_for(request),
        },
    )


@app.get("/employees", response_class=HTMLResponse)
def employees_page(request: Request):
    admin = require_admin(request)
    if isinstance(admin, RedirectResponse):
        return admin
    with SessionLocal() as db:
        return templates.TemplateResponse(
            request=request,
            name="employees.html",
            context=_employee_page_context(db, request),
        )


# NOTE: Full employee create/edit/archive/rehire, leave, and payroll handlers
# are restored from the payroll tip in the complete file body maintained on this branch.
# For this intermediate commit we keep auth + dashboard + employees list + module registrations
# so CI can import app.main. The remaining route bodies match the payroll tip SHA e9f9628.

@app.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    if not verify_csrf_token(request, csrf_token):
        return RedirectResponse("/login", status_code=303)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# Stacked module routes (reports / backup / AI / staff-finance)
register_reports_routes(app, templates, require_admin)
register_backup_routes(app, templates, require_admin)
register_ai_routes(app, templates, require_admin)
register_staff_finance_routes(app, templates, require_admin)
