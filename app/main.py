from datetime import date
from decimal import Decimal

from fastapi import Depends, FastAPI, Form, HTTPException, Request
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
from app.employee_codes import allocate_employee_code
from app.models import AdminAccount, AuditLog, Branch, Department, Employee, EmployeeChange, EmployeeServicePeriod

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


@app.exception_handler(HTTPException)
async def authentication_exception_handler(request: Request, exc: HTTPException):
    """Redirect browser page requests to login while preserving API error responses."""
    accept = request.headers.get("accept", "").lower()
    wants_html = "text/html" in accept and "application/json" not in accept
    if exc.status_code == 401 and request.method in {"GET", "HEAD"} and wants_html:
        return RedirectResponse("/login", status_code=303)
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)


@app.get("/health")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail={"status": "degraded", "database": "unavailable"})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="login.html",
        context={"error": None, "csrf_token": get_csrf_token(request)},
    )


@app.post("/login", dependencies=[Depends(csrf_protect)])
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    with SessionLocal() as db:
        admin = authenticate(db, username.strip(), password)
        if admin is None:
            return templates.TemplateResponse(
                request=request, name="login.html",
                context={"error": "اسم المستخدم أو كلمة المرور غير صحيحة.", "csrf_token": get_csrf_token(request)},
                status_code=401,
            )
        request.session.clear()
        request.session["admin_username"] = admin.username
        request.session["admin_session_version"] = admin.session_version
        get_csrf_token(request)
    return RedirectResponse("/", status_code=303)


@app.post("/logout", dependencies=[Depends(csrf_protect)])
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, admin: AdminAccount = Depends(get_current_admin)):
    return templates.TemplateResponse(
        request=request, name="dashboard.html",
        context={"app_name": settings.app_name, "admin": admin.username, "csrf_token": get_csrf_token(request)},
    )


@app.post("/departments", dependencies=[Depends(csrf_protect)])
def create_department(
    name: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="اسم القسم مطلوب.")
    with SessionLocal.begin() as db:
        if db.scalar(select(Department.id).where(Department.name == name)):
            raise HTTPException(status_code=409, detail="اسم القسم مستخدم بالفعل.")
        department = Department(name=name, active=True)
        db.add(department)
        db.flush()
        db.add(AuditLog(
            actor=admin.username, action="department_created",
            entity_type="department", entity_id=str(department.id),
            new_value=name, reason="Department created by HR",
        ))
    return RedirectResponse("/employees", status_code=303)


@app.post("/departments/{department_id}/edit", dependencies=[Depends(csrf_protect)])
def edit_department(
    department_id: int,
    name: str = Form(...),
    active: bool = Form(False),
    admin: AdminAccount = Depends(get_current_admin),
):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="اسم القسم مطلوب.")
    with SessionLocal.begin() as db:
        department = db.get(Department, department_id)
        if department is None:
            raise HTTPException(status_code=404, detail="القسم غير موجود.")
        duplicate = db.scalar(select(Department.id).where(
            Department.name == name, Department.id != department_id
        ))
        if duplicate:
            raise HTTPException(status_code=409, detail="اسم القسم مستخدم بالفعل.")
        if not active:
            active_employee = db.scalar(select(Employee.id).where(
                Employee.department_id == department_id, Employee.status == "active"
            ).limit(1))
            if active_employee is not None:
                raise HTTPException(status_code=409, detail="انقل الموظفين النشطين إلى قسم آخر قبل إيقاف هذا القسم.")
        old_value = f"name={department.name}; active={department.active}"
        department.name = name
        department.active = active
        db.add(AuditLog(
            actor=admin.username, action="department_updated",
            entity_type="department", entity_id=str(department.id),
            old_value=old_value, new_value=f"name={name}; active={active}",
            reason="Department settings updated",
        ))
    return RedirectResponse("/employees", status_code=303)


@app.post("/branches", dependencies=[Depends(csrf_protect)])
def create_branch(
    department_id: int = Form(...),
    name: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="اسم الفرع مطلوب.")
    with SessionLocal.begin() as db:
        department = db.get(Department, department_id)
        if department is None or not department.active:
            raise HTTPException(status_code=400, detail="القسم المحدد غير متاح.")
        duplicate = db.scalar(select(Branch.id).where(
            Branch.department_id == department_id, Branch.name == name
        ))
        if duplicate:
            raise HTTPException(status_code=409, detail="اسم الفرع مستخدم داخل هذا القسم.")
        branch = Branch(department_id=department_id, name=name, active=True)
        db.add(branch)
        db.flush()
        db.add(AuditLog(
            actor=admin.username, action="branch_created",
            entity_type="branch", entity_id=str(branch.id),
            new_value=f"{department.name}/{name}", reason="Branch created by HR",
        ))
    return RedirectResponse("/employees", status_code=303)


@app.post("/branches/{branch_id}/edit", dependencies=[Depends(csrf_protect)])
def edit_branch(
    branch_id: int,
    department_id: int = Form(...),
    name: str = Form(...),
    active: bool = Form(False),
    admin: AdminAccount = Depends(get_current_admin),
):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="اسم الفرع مطلوب.")
    with SessionLocal.begin() as db:
        branch = db.get(Branch, branch_id)
        if branch is None:
            raise HTTPException(status_code=404, detail="الفرع غير موجود.")
        department = db.get(Department, department_id)
        if department is None or not department.active:
            raise HTTPException(status_code=400, detail="القسم المحدد غير متاح.")
        duplicate = db.scalar(select(Branch.id).where(
            Branch.department_id == department_id,
            Branch.name == name,
            Branch.id != branch_id,
        ))
        if duplicate:
            raise HTTPException(status_code=409, detail="اسم الفرع مستخدم داخل هذا القسم.")
        if not active:
            assigned = db.scalar(select(Employee.id).where(
                Employee.branch_id == branch_id, Employee.status == "active"
            ).limit(1))
            if assigned is not None:
                raise HTTPException(status_code=409, detail="انقل الموظفين النشطين من هذا الفرع قبل إيقافه.")
        old_value = f"department_id={branch.department_id}; name={branch.name}; active={branch.active}"
        branch.department_id = department_id
        branch.name = name
        branch.active = active
        db.add(AuditLog(
            actor=admin.username, action="branch_updated",
            entity_type="branch", entity_id=str(branch.id),
            old_value=old_value,
            new_value=f"department_id={department_id}; name={name}; active={active}",
            reason="Branch settings updated",
        ))
    return RedirectResponse("/employees", status_code=303)


@app.get("/employees", response_class=HTMLResponse)
def employees_page(request: Request, admin: AdminAccount = Depends(get_current_admin)):
    with SessionLocal() as db:
        departments = db.scalars(select(Department).where(Department.active.is_(True)).order_by(Department.name)).all()
        branches = db.scalars(select(Branch).where(Branch.active.is_(True)).order_by(Branch.name)).all()
        employees = db.scalars(select(Employee).order_by(Employee.code)).all()
        return templates.TemplateResponse(
            request=request, name="employees.html",
            context={"departments": departments, "branches": branches, "employees": employees, "csrf_token": get_csrf_token(request)},
        )


@app.post("/employees", dependencies=[Depends(csrf_protect)])
def create_employee(
    request: Request,
    full_name: str = Form(...),
    national_id: str = Form(""),
    phone: str = Form(""),
    address: str = Form(""),
    department_id: int = Form(...),
    branch_id: int | None = Form(None),
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
        branch = db.get(Branch, branch_id) if branch_id is not None else None
        if branch_id is not None and (branch is None or not branch.active or branch.department_id != department_id):
            return HTMLResponse("الفرع المحدد غير تابع للقسم المختار أو غير متاح.", status_code=400)
        employee = Employee(
            code=allocate_employee_code(db), full_name=full_name.strip(),
            national_id=national_id.strip() or None, phone=phone.strip() or None,
            address=address.strip() or None, department_id=department_id,
            branch_id=branch_id, position=position.strip(), hire_date=hire_date,
            basic_salary=basic_salary, daily_hours=daily_hours,
            fingerprint_id=fingerprint_id.strip() or None, status="active",
        )
        db.add(employee)
        db.flush()
        db.add(EmployeeServicePeriod(employee_id=employee.id, start_date=hire_date, start_reason="Initial employment"))
        db.commit()
    return RedirectResponse("/employees", status_code=303)


@app.post("/employees/{employee_id}/edit", dependencies=[Depends(csrf_protect)])
def edit_employee(
    employee_id: int,
    full_name: str = Form(...),
    national_id: str = Form(""),
    phone: str = Form(""),
    address: str = Form(""),
    department_id: int = Form(...),
    branch_id: int | None = Form(None),
    position: str = Form(...),
    basic_salary: Decimal = Form(...),
    daily_hours: Decimal = Form(...),
    fingerprint_id: str = Form(""),
    effective_date: date = Form(...),
    reason: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    reason = reason.strip()
    full_name = full_name.strip()
    position = position.strip()
    if not reason or not full_name or not position:
        raise HTTPException(status_code=400, detail="الاسم والمسمى وسبب التعديل حقول مطلوبة.")
    if basic_salary < 0 or daily_hours <= 0 or daily_hours > 24:
        raise HTTPException(status_code=400, detail="بيانات الراتب أو ساعات العمل غير صحيحة.")

    with SessionLocal.begin() as db:
        employee = db.get(Employee, employee_id)
        if employee is None:
            raise HTTPException(status_code=404, detail="الموظف غير موجود.")
        department = db.get(Department, department_id)
        if department is None or not department.active:
            raise HTTPException(status_code=400, detail="القسم المحدد غير متاح.")
        if branch_id is not None:
            branch = db.get(Branch, branch_id)
            if branch is None or not branch.active or branch.department_id != department_id:
                raise HTTPException(status_code=400, detail="الفرع المحدد غير تابع للقسم المختار أو غير متاح.")

        normalized = {
            "full_name": full_name,
            "national_id": national_id.strip() or None,
            "phone": phone.strip() or None,
            "address": address.strip() or None,
            "department_id": department_id,
            "branch_id": branch_id,
            "position": position,
            "basic_salary": basic_salary,
            "daily_hours": daily_hours,
            "fingerprint_id": fingerprint_id.strip() or None,
        }
        for field_name in ("national_id", "fingerprint_id"):
            value = normalized[field_name]
            if value is None:
                continue
            duplicate = db.scalar(
                select(Employee.id).where(
                    getattr(Employee, field_name) == value,
                    Employee.id != employee.id,
                ).limit(1)
            )
            if duplicate is not None:
                raise HTTPException(status_code=409, detail=f"القيمة المدخلة في {field_name} مستخدمة لموظف آخر.")

        changes = []
        for field_name, new_value in normalized.items():
            old_value = getattr(employee, field_name)
            if old_value != new_value:
                changes.append((field_name, old_value, new_value))
        for field_name, old_value, new_value in changes:
            setattr(employee, field_name, new_value)
            db.add(EmployeeChange(
                employee_id=employee.id,
                field_name=field_name,
                old_value=str(old_value) if old_value is not None else None,
                new_value=str(new_value) if new_value is not None else None,
                effective_date=effective_date,
                reason=reason,
            ))
        if changes:
            db.add(AuditLog(
                actor=admin.username,
                action="employee_updated",
                entity_type="employee",
                entity_id=str(employee.id),
                old_value="; ".join(f"{name}={old}" for name, old, _ in changes),
                new_value="; ".join(f"{name}={new}" for name, _, new in changes),
                reason=reason,
            ))
    return RedirectResponse("/employees", status_code=303)



@app.post("/employees/{employee_id}/archive", dependencies=[Depends(csrf_protect)])
def archive_employee(
    employee_id: int,
    reason: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    reason = reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="سبب الأرشفة مطلوب.")
    with SessionLocal.begin() as db:
        employee = db.get(Employee, employee_id)
        if employee is None:
            raise HTTPException(status_code=404, detail="الموظف غير موجود.")
        if employee.status != "active":
            raise HTTPException(status_code=409, detail="الموظف ليس على رأس العمل.")
        period = db.scalar(
            select(EmployeeServicePeriod).where(
                EmployeeServicePeriod.employee_id == employee.id,
                EmployeeServicePeriod.end_date.is_(None),
            )
        )
        if period is None:
            raise HTTPException(status_code=409, detail="لا توجد فترة خدمة مفتوحة لهذا الموظف.")
        if date.today() < period.start_date:
            raise HTTPException(status_code=400, detail="لا يمكن إنهاء فترة خدمة قبل تاريخ بدايتها.")
        period.end_date = date.today()
        period.end_reason = reason
        employee.status = "archived"
        db.add(AuditLog(
            actor=admin.username, action="employee_archived",
            entity_type="employee", entity_id=str(employee.id),
            old_value="active", new_value="archived", reason=reason,
        ))
    return RedirectResponse("/employees", status_code=303)


@app.post("/employees/{employee_id}/rehire", dependencies=[Depends(csrf_protect)])
def rehire_employee(
    employee_id: int,
    new_hire_date: date = Form(...),
    reason: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    reason = reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="سبب إعادة التعيين مطلوب.")
    with SessionLocal.begin() as db:
        employee = db.get(Employee, employee_id)
        if employee is None:
            raise HTTPException(status_code=404, detail="الموظف غير موجود.")
        if employee.status != "archived":
            raise HTTPException(status_code=409, detail="لا يمكن إعادة تعيين موظف غير مؤرشف.")
        previous_period = db.scalar(
            select(EmployeeServicePeriod)
            .where(EmployeeServicePeriod.employee_id == employee.id)
            .order_by(EmployeeServicePeriod.id.desc())
            .limit(1)
        )
        if previous_period is None or previous_period.end_date is None:
            raise HTTPException(status_code=409, detail="سجل فترة الخدمة السابقة غير مكتمل؛ راجع بيانات الأرشفة أولاً.")
        if new_hire_date <= previous_period.end_date:
            raise HTTPException(status_code=400, detail="تاريخ إعادة التعيين يجب أن يكون بعد انتهاء فترة الخدمة السابقة.")
        employee.status = "active"
        employee.hire_date = new_hire_date
        db.add(EmployeeServicePeriod(
            employee_id=employee.id, start_date=new_hire_date, start_reason=reason,
        ))
        db.add(AuditLog(
            actor=admin.username, action="employee_rehired",
            entity_type="employee", entity_id=str(employee.id),
            old_value="archived", new_value="active", reason=reason,
        ))
    return RedirectResponse("/employees", status_code=303)
