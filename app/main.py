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
from app.models import AdminAccount, AuditLog, Department, Employee, EmployeeChange
from app.security import csrf_token_for, rotate_csrf_token, verify_csrf_token


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
        # Detach-safe scalar values only are used by route handlers and templates.
        return admin


def _employee_page_context(db: Session, request: Request, *, error: str | None = None):
    departments = db.scalars(
        select(Department)
        .where(Department.active.is_(True))
        .order_by(Department.name)
    ).all()
    employees = db.scalars(
        select(Employee).order_by(Employee.code)
    ).all()
    return {
        "departments": departments,
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


@app.post("/employees", response_class=HTMLResponse)
def create_employee(
    request: Request,
    full_name: str = Form(...),
    national_id: str = Form(""),
    phone: str = Form(""),
    department_id: int = Form(...),
    position: str = Form(...),
    hire_date: date = Form(...),
    basic_salary: Decimal = Form(...),
    daily_hours: Decimal = Form(...),
    fingerprint_id: str = Form(""),
    csrf_token: str = Form(...),
):
    admin = require_admin(request)
    if isinstance(admin, RedirectResponse):
        return admin
    if not verify_csrf_token(request, csrf_token):
        with SessionLocal() as db:
            return templates.TemplateResponse(
                request=request,
                name="employees.html",
                context=_employee_page_context(
                    db, request, error="انتهت صلاحية النموذج. حدّث الصفحة وحاول مرة أخرى."
                ),
                status_code=403,
            )

    clean_name = full_name.strip()
    clean_position = position.strip()
    if (
        not clean_name
        or not clean_position
        or basic_salary < 0
        or daily_hours <= 0
        or daily_hours > 24
    ):
        with SessionLocal() as db:
            return templates.TemplateResponse(
                request=request,
                name="employees.html",
                context=_employee_page_context(
                    db, request, error="راجع الاسم والمسمى والراتب وساعات العمل."
                ),
                status_code=400,
            )

    try:
        with SessionLocal() as db:
            department = db.get(Department, department_id)
            if department is None or not department.active:
                return templates.TemplateResponse(
                    request=request,
                    name="employees.html",
                    context=_employee_page_context(
                        db, request, error="القسم المحدد غير متاح."
                    ),
                    status_code=400,
                )

            employee_code = reserve_employee_code(db)
            employee = Employee(
                code=employee_code,
                full_name=clean_name,
                national_id=national_id.strip() or None,
                phone=phone.strip() or None,
                department_id=department_id,
                position=clean_position,
                hire_date=hire_date,
                basic_salary=basic_salary,
                daily_hours=daily_hours,
                fingerprint_id=fingerprint_id.strip() or None,
                status="active",
            )
            db.add(employee)
            db.flush()

            initial_values = {
                "code": employee.code,
                "full_name": employee.full_name,
                "department_id": department_id,
                "position": clean_position,
                "hire_date": hire_date.isoformat(),
                "basic_salary": str(basic_salary),
                "daily_hours": str(daily_hours),
            }
            db.add(EmployeeChange(
                employee_id=employee.id,
                field_name="initial_record",
                old_value=None,
                new_value=json.dumps(initial_values, ensure_ascii=False),
                effective_date=hire_date,
                reason="Initial employee record",
            ))
            db.add(AuditLog(
                actor=admin.username,
                action="employee_created",
                entity_type="employee",
                entity_id=str(employee.id),
                new_value=json.dumps(initial_values, ensure_ascii=False),
                reason="Initial employee registration",
            ))
            db.commit()
    except IntegrityError:
        with SessionLocal() as db:
            return templates.TemplateResponse(
                request=request,
                name="employees.html",
                context=_employee_page_context(
                    db,
                    request,
                    error="يوجد رقم قومي أو رقم بصمة مسجل بالفعل. راجع البيانات."
                ),
                status_code=409,
            )

    return RedirectResponse("/employees", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    if not verify_csrf_token(request, csrf_token):
        return RedirectResponse("/login", status_code=303)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
