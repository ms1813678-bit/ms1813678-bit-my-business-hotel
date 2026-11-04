from datetime import date
from decimal import Decimal

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.sessions import SessionMiddleware

from app.auth import authenticate
from app.config import settings
from app.csrf import csrf_protect, get_csrf_token
from app.db import SessionLocal, engine
from app.dependencies import get_current_admin
from app.models import AdminAccount, Department, Employee

app = FastAPI(title=settings.app_name, docs_url=None if settings.app_env == "production" else "/docs")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="session",
    max_age=8 * 60 * 60,
    path="/",
    same_site="lax",
    https_only=settings.app_env.lower() == "production",
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


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
        context={"error": None, "csrf_token": get_csrf_token(request)},
    )


@app.post("/login", dependencies=[Depends(csrf_protect)])
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    with SessionLocal() as db:
        admin = authenticate(db, username.strip(), password)
        if admin is None:
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "error": "اسم المستخدم أو كلمة المرور غير صحيحة.",
                    "csrf_token": get_csrf_token(request),
                },
                status_code=401,
            )
        request.session.clear()
        request.session["admin_username"] = admin.username
        # Rotate the CSRF token when the anonymous session becomes authenticated.
        csrf_token = get_csrf_token(request)
    return RedirectResponse("/", status_code=303)


@app.post("/logout", dependencies=[Depends(csrf_protect)])
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    admin: AdminAccount = Depends(get_current_admin),
):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "app_name": settings.app_name,
            "admin": admin.username,
            "csrf_token": get_csrf_token(request),
        },
    )


@app.get("/employees", response_class=HTMLResponse)
def employees_page(
    request: Request,
    admin: AdminAccount = Depends(get_current_admin),
):
    with SessionLocal() as db:
        departments = db.scalars(
            select(Department)
            .where(Department.active.is_(True))
            .order_by(Department.name)
        ).all()
        employees = db.scalars(select(Employee).order_by(Employee.code)).all()
        return templates.TemplateResponse(
            request=request,
            name="employees.html",
            context={
                "departments": departments,
                "employees": employees,
                "csrf_token": get_csrf_token(request),
            },
        )


@app.post("/employees", dependencies=[Depends(csrf_protect)])
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
    admin: AdminAccount = Depends(get_current_admin),
):
    if basic_salary < 0 or daily_hours <= 0 or daily_hours > 24:
        return HTMLResponse("بيانات الراتب أو ساعات العمل غير صحيحة.", status_code=400)
    with SessionLocal() as db:
        department = db.get(Department, department_id)
        if department is None or not department.active:
            return HTMLResponse("القسم المحدد غير متاح.", status_code=400)
        last_code = db.scalar(select(Employee.code).order_by(Employee.id.desc()).limit(1))
        next_number = int(last_code.split("-")[1]) + 1 if last_code and last_code.startswith("MY-") else 1
        employee = Employee(
            code=f"MY-{next_number:04d}",
            full_name=full_name.strip(),
            national_id=national_id.strip() or None,
            phone=phone.strip() or None,
            department_id=department_id,
            position=position.strip(),
            hire_date=hire_date,
            basic_salary=basic_salary,
            daily_hours=daily_hours,
            fingerprint_id=fingerprint_id.strip() or None,
            status="active",
        )
        db.add(employee)
        db.commit()
    return RedirectResponse("/employees", status_code=303)
