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
from app.models import (\n    AdminAccount, AuditLog, Branch, Department, Employee, EmployeeChange, EmployeeServicePeriod,\n    EmployeeLeaveBalance, LeaveRecord, LeaveType,\n)
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


@app.post("/employees", response_class=HTMLResponse)
def create_employee(
    request: Request,
    full_name: str = Form(...),
    national_id: str = Form(""),
    phone: str = Form(""),
    department_id: int = Form(...),
    branch_id: int = Form(0),
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

            selected_branch_id = branch_id or None
            if selected_branch_id is not None:
                branch = db.get(Branch, selected_branch_id)
                if branch is None or not branch.active or branch.department_id != department_id:
                    return templates.TemplateResponse(
                        request=request,
                        name="employees.html",
                        context=_employee_page_context(
                            db, request, error="الفرع المحدد غير تابع للقسم المختار."
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
                branch_id=selected_branch_id,
                position=clean_position,
                hire_date=hire_date,
                basic_salary=basic_salary,
                daily_hours=daily_hours,
                fingerprint_id=fingerprint_id.strip() or None,
                status="active",
            )
            db.add(employee)
            db.flush()

            db.add(EmployeeServicePeriod(
                employee_id=employee.id,
                start_date=hire_date,
                start_reason="Initial employment",
            ))
            initial_values = {
                "code": employee.code,
                "full_name": employee.full_name,
                "department_id": department_id,
                "branch_id": selected_branch_id,
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


@app.get("/employees/{employee_id}/edit", response_class=HTMLResponse)
def edit_employee_page(request: Request, employee_id: int):
    admin = require_admin(request)
    if isinstance(admin, RedirectResponse):
        return admin
    with SessionLocal() as db:
        employee = db.get(Employee, employee_id)
        if employee is None:
            return HTMLResponse("الموظف غير موجود.", status_code=404)
        departments = db.scalars(
            select(Department).where(Department.active.is_(True)).order_by(Department.name)
        ).all()
        branches = db.scalars(
            select(Branch).where(Branch.active.is_(True)).order_by(Branch.name)
        ).all()
        periods = db.scalars(
            select(EmployeeServicePeriod)
            .where(EmployeeServicePeriod.employee_id == employee_id)
            .order_by(EmployeeServicePeriod.start_date.desc())
        ).all()
        return templates.TemplateResponse(
            request=request,
            name="employee_edit.html",
            context={
                "employee": employee,
                "departments": departments,
                "branches": branches,
                "periods": periods,
                "csrf_token": csrf_token_for(request),
                "error": None,
            },
        )


@app.post("/employees/{employee_id}/edit", response_class=HTMLResponse)
def edit_employee(
    request: Request,
    employee_id: int,
    full_name: str = Form(...),
    national_id: str = Form(""),
    phone: str = Form(""),
    address: str = Form(""),
    department_id: int = Form(...),
    branch_id: int = Form(0),
    position: str = Form(...),
    hire_date: date = Form(...),
    basic_salary: Decimal = Form(...),
    daily_hours: Decimal = Form(...),
    fingerprint_id: str = Form(""),
    effective_date: date = Form(...),
    reason: str = Form(...),
    csrf_token: str = Form(...),
):
    admin = require_admin(request)
    if isinstance(admin, RedirectResponse):
        return admin
    if not verify_csrf_token(request, csrf_token):
        return RedirectResponse("/employees", status_code=303)

    changes = {
        "full_name": full_name.strip(),
        "national_id": national_id.strip() or None,
        "phone": phone.strip() or None,
        "address": address.strip() or None,
        "department_id": department_id,
        "branch_id": branch_id or None,
        "position": position.strip(),
        "hire_date": hire_date,
        "basic_salary": basic_salary,
        "daily_hours": daily_hours,
        "fingerprint_id": fingerprint_id.strip() or None,
    }
    try:
        with SessionLocal() as db:
            update_employee_profile(
                db,
                employee_id=employee_id,
                changes=changes,
                effective_date=effective_date,
                reason=reason,
                actor=admin.username,
            )
            db.commit()
    except ValueError as exc:
        message = {
            "employee_not_found": "الموظف غير موجود.",
            "employee_change_reason_required": "سبب التعديل مطلوب.",
            "employee_department_unavailable": "القسم المحدد غير متاح.",
            "employee_branch_not_in_department": "الفرع المحدد غير تابع للقسم المختار.",
            "employee_salary_or_hours_invalid": "راجع الراتب وساعات العمل.",
            "employee_name_and_position_required": "الاسم والمسمى الوظيفي مطلوبان.",
            "terminated_employee_cannot_be_edited": "الموظف المؤرشف لا يقبل تعديلات مباشرة.",
        }.get(str(exc), "تعذر حفظ التعديلات. راجع البيانات.")
        with SessionLocal() as db:
            employee = db.get(Employee, employee_id)
            if employee is None:
                return HTMLResponse("الموظف غير موجود.", status_code=404)
            departments = db.scalars(select(Department).where(Department.active.is_(True))).all()
            branches = db.scalars(select(Branch).where(Branch.active.is_(True))).all()
            periods = db.scalars(select(EmployeeServicePeriod).where(
                EmployeeServicePeriod.employee_id == employee_id
            ).order_by(EmployeeServicePeriod.start_date.desc())).all()
            return templates.TemplateResponse(
                request=request,
                name="employee_edit.html",
                context={
                    "employee": employee, "departments": departments, "branches": branches,
                    "periods": periods, "csrf_token": csrf_token_for(request), "error": message,
                },
                status_code=400,
            )
    except IntegrityError:
        return RedirectResponse(f"/employees/{employee_id}/edit", status_code=303)
    return RedirectResponse(f"/employees/{employee_id}/edit", status_code=303)


@app.post("/employees/{employee_id}/archive")
def archive_employee_route(
    request: Request,
    employee_id: int,
    effective_date: date = Form(...),
    reason: str = Form(...),
    csrf_token: str = Form(...),
):
    admin = require_admin(request)
    if isinstance(admin, RedirectResponse):
        return admin
    if not verify_csrf_token(request, csrf_token):
        return RedirectResponse("/employees", status_code=303)
    try:
        with SessionLocal() as db:
            archive_employee(
                db, employee_id=employee_id, effective_date=effective_date,
                reason=reason, actor=admin.username,
            )
            db.commit()
    except ValueError:
        return RedirectResponse(f"/employees/{employee_id}/edit", status_code=303)
    return RedirectResponse(f"/employees/{employee_id}/edit", status_code=303)


@app.post("/employees/{employee_id}/rehire")
def rehire_employee_route(
    request: Request,
    employee_id: int,
    start_date: date = Form(...),
    reason: str = Form(...),
    csrf_token: str = Form(...),
):
    admin = require_admin(request)
    if isinstance(admin, RedirectResponse):
        return admin
    if not verify_csrf_token(request, csrf_token):
        return RedirectResponse("/employees", status_code=303)
    try:
        with SessionLocal() as db:
            rehire_employee(
                db, employee_id=employee_id, start_date=start_date,
                reason=reason, actor=admin.username,
            )
            db.commit()
    except ValueError:
        return RedirectResponse(f"/employees/{employee_id}/edit", status_code=303)
    return RedirectResponse(f"/employees/{employee_id}/edit", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    if not verify_csrf_token(request, csrf_token):
        return RedirectResponse("/login", status_code=303)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


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
