from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
import secrets
import time as time_module

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from PIL import Image, UnidentifiedImageError
from starlette.middleware.sessions import SessionMiddleware

from app.auth import authenticate
from app.config import settings
from app.csrf import csrf_protect, get_csrf_token
from app.db import SessionLocal, engine
from app.dependencies import get_current_admin
from app.employee_codes import allocate_employee_code
from app.models import AdminAccount, AuditLog, Branch, Department, Employee, EmployeeChange, EmployeeDataChangeRequest, EmployeeServicePeriod
from app.security_roster_import import parse_security_roster_xlsx
from app.security_roster_workflow import prepare_security_roster, confirm_security_roster_day

SECURITY_ROSTER_MAX_BYTES = 5 * 1024 * 1024
SECURITY_ROSTER_STAGING_DIR = Path("var") / "security_roster_staging"
SECURITY_ROSTER_STAGING_TTL_SECONDS = 60 * 60

EMPLOYEE_PHOTO_DIR = Path("var") / "employee_photos"
EMPLOYEE_PHOTO_MAX_BYTES = 5 * 1024 * 1024
EMPLOYEE_PHOTO_FIELDS = {"full_name", "phone", "address", "position", "national_id"}


def _store_employee_photo(upload: UploadFile | None) -> str | None:
    if upload is None or not upload.filename:
        return None
    raw = upload.file.read(EMPLOYEE_PHOTO_MAX_BYTES + 1)
    if not raw:
        return None
    if len(raw) > EMPLOYEE_PHOTO_MAX_BYTES:
        raise HTTPException(status_code=413, detail="حجم الصورة يجب ألا يتجاوز 5 ميجابايت.")
    try:
        import io
        with Image.open(io.BytesIO(raw)) as image_file:
            image_file.verify()
            image_format = image_file.format
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(status_code=400, detail="ملف الصورة غير صالح.")
    extensions = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
    extension = extensions.get(image_format)
    if extension is None:
        raise HTTPException(status_code=400, detail="الصورة يجب أن تكون JPG أو PNG أو WEBP.")
    EMPLOYEE_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{extension}"
    (EMPLOYEE_PHOTO_DIR / filename).write_bytes(raw)
    return filename


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


@app.get("/security-roster", response_class=HTMLResponse)
def security_roster_page(
    request: Request,
    admin: AdminAccount = Depends(get_current_admin),
):
    return templates.TemplateResponse(
        request=request,
        name="security_roster.html",
        context={
            "admin": admin.username,
            "csrf_token": get_csrf_token(request),
            "days": [],
            "issues": [],
            "error": None,
            "filename": None,
        },
    )


def _stage_security_roster(raw: bytes, request: Request) -> str:
    SECURITY_ROSTER_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    try:
        SECURITY_ROSTER_STAGING_DIR.chmod(0o700)
    except OSError:
        pass
    now = time_module.time()
    for candidate in SECURITY_ROSTER_STAGING_DIR.glob("*.xlsx"):
        try:
            if now - candidate.stat().st_mtime > SECURITY_ROSTER_STAGING_TTL_SECONDS:
                candidate.unlink(missing_ok=True)
        except OSError:
            continue
    token = secrets.token_urlsafe(32)
    path = SECURITY_ROSTER_STAGING_DIR / f"{token}.xlsx"
    path.write_bytes(raw)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    request.session["security_roster_stage"] = token
    return token


def _load_staged_security_roster(token: str, request: Request) -> bytes:
    expected = request.session.get("security_roster_stage")
    if not isinstance(expected, str) or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="انتهت صلاحية معاينة الملف. ارفع الملف مرة أخرى.")
    if not token or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in token):
        raise HTTPException(status_code=400, detail="رمز المعاينة غير صالح.")
    path = SECURITY_ROSTER_STAGING_DIR / f"{token}.xlsx"
    try:
        if time_module.time() - path.stat().st_mtime > SECURITY_ROSTER_STAGING_TTL_SECONDS:
            path.unlink(missing_ok=True)
            raise HTTPException(status_code=410, detail="انتهت صلاحية معاينة الملف. ارفع الملف مرة أخرى.")
        raw = path.read_bytes()
    except FileNotFoundError:
        raise HTTPException(status_code=410, detail="ملف المعاينة غير موجود. ارفع الملف مرة أخرى.")
    if len(raw) > SECURITY_ROSTER_MAX_BYTES:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail="حجم ملف جدول الأمن غير صالح.")
    return raw


def _clear_staged_security_roster(token: str, request: Request) -> None:
    if request.session.get("security_roster_stage") == token:
        request.session.pop("security_roster_stage", None)
    if token and all(ch in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in token):
        (SECURITY_ROSTER_STAGING_DIR / f"{token}.xlsx").unlink(missing_ok=True)


def _read_security_roster_upload(upload: UploadFile) -> bytes:
    filename = (upload.filename or "").strip()
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="يرجى رفع ملف Excel بصيغة .xlsx.")
    raw = upload.file.read(SECURITY_ROSTER_MAX_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="الملف فارغ.")
    if len(raw) > SECURITY_ROSTER_MAX_BYTES:
        raise HTTPException(status_code=413, detail="حجم ملف جدول الأمن يجب ألا يتجاوز 5 ميجابايت.")
    return raw


def _security_roster_aliases(db):
    from sqlalchemy import select
    from app.models import Department, Employee
    department = db.scalar(select(Department).where(
        Department.name == "Security", Department.active.is_(True)
    ))
    if department is None:
        raise ValueError("security_department_not_configured")
    employees = db.scalars(select(Employee).where(
        Employee.department_id == department.id,
        Employee.status == "active",
    ).order_by(Employee.id)).all()
    if len(employees) != 3:
        raise ValueError("security_department_must_have_exactly_three_active_employees")
    aliases = {}
    normalized_owners = {}
    for employee in employees:
        for value in (employee.full_name, employee.code, employee.fingerprint_id):
            if not value:
                continue
            key = " ".join(str(value).casefold().split())
            owner = normalized_owners.get(key)
            if owner is not None and owner != employee.id:
                raise ValueError("security_employee_identifiers_are_ambiguous_for_excel_mapping")
            normalized_owners[key] = employee.id
            aliases[str(value)] = employee.id
    if len({value for value in aliases.values()}) != 3:
        raise ValueError("security_employee_identifiers_must_resolve_to_three_employees")
    return aliases


@app.post("/security-roster/preview", response_class=HTMLResponse, dependencies=[Depends(csrf_protect)])
def security_roster_preview(
    request: Request,
    file: UploadFile = File(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        raw = _read_security_roster_upload(file)
        with SessionLocal() as db:
            aliases = _security_roster_aliases(db)
            parsed = parse_security_roster_xlsx(raw, aliases)
            prepared = prepare_security_roster(db, parsed)
        stage_token = _stage_security_roster(raw, request)
        return templates.TemplateResponse(
            request=request,
            name="security_roster.html",
            context={
                "admin": admin.username,
                "csrf_token": get_csrf_token(request),
                "days": prepared["days"],
                "issues": prepared["import_issues"],
                "error": None,
                "filename": file.filename,
                "stage_token": stage_token,
            },
        )
    except (ValueError, KeyError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="security_roster.html",
            context={
                "admin": admin.username,
                "csrf_token": get_csrf_token(request),
                "days": [],
                "issues": [],
                "error": str(exc),
                "filename": file.filename,
            },
            status_code=400,
        )


@app.post("/security-roster/confirm", response_class=HTMLResponse, dependencies=[Depends(csrf_protect)])
def security_roster_confirm(
    request: Request,
    work_date: str = Form(...),
    stage_token: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    from datetime import date
    try:
        raw = _load_staged_security_roster(stage_token, request)
        selected_date = date.fromisoformat(work_date)
        with SessionLocal.begin() as db:
            aliases = _security_roster_aliases(db)
            parsed = parse_security_roster_xlsx(raw, aliases)
            prepared = prepare_security_roster(db, parsed)
            selected = next(
                (item for item in prepared["days"] if item["work_date"] == selected_date),
                None,
            )
            if selected is None or not selected["can_confirm"]:
                raise ValueError("security_roster_day_not_ready_for_approval")
            confirm_security_roster_day(
                db,
                selected_date,
                selected["roster"],
                admin,
                import_issues=parsed.get("issues", []),
            )
        _clear_staged_security_roster(stage_token, request)
        return templates.TemplateResponse(
            request=request,
            name="security_roster.html",
            context={
                "admin": admin.username,
                "csrf_token": get_csrf_token(request),
                "days": [],
                "issues": [],
                "error": None,
                "success": f"تم اعتماد جدول يوم {selected_date.isoformat()} بنجاح.",
                "filename": "ملف جدول الأمن",
            },
        )
    except (ValueError, KeyError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="security_roster.html",
            context={
                "admin": admin.username,
                "csrf_token": get_csrf_token(request),
                "days": [],
                "issues": [],
                "error": str(exc),
                "filename": "ملف جدول الأمن",
            },
            status_code=400,
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
        any_assignment = db.scalar(select(Employee.id).where(
            Employee.branch_id == branch_id
        ).limit(1))
        if any_assignment is not None and department_id != branch.department_id:
            raise HTTPException(status_code=409, detail="لا يمكن تغيير القسم التابع للفرع بعد ربط موظفين به؛ أنشئ فرعًا جديدًا للحفاظ على السجل التاريخي.")
        active_assignment = db.scalar(select(Employee.id).where(
            Employee.branch_id == branch_id, Employee.status == "active"
        ).limit(1))
        if not active and active_assignment is not None:
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


@app.get("/employees/{employee_id}/photo")
def employee_photo(
    employee_id: int,
    admin: AdminAccount = Depends(get_current_admin),
):
    """Serve an employee photo only to an authenticated administrator."""
    with SessionLocal() as db:
        employee = db.get(Employee, employee_id)
        if employee is None or not employee.photo_path:
            raise HTTPException(status_code=404, detail="صورة الموظف غير موجودة.")

        # Only a stored basename is accepted; never allow a database value to
        # escape the private photo directory.
        filename = Path(employee.photo_path).name
        if filename != employee.photo_path:
            raise HTTPException(status_code=404, detail="صورة الموظف غير موجودة.")
        photo_path = EMPLOYEE_PHOTO_DIR / filename
        try:
            photo_path.resolve().relative_to(EMPLOYEE_PHOTO_DIR.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="صورة الموظف غير موجودة.")
        if not photo_path.is_file():
            raise HTTPException(status_code=404, detail="صورة الموظف غير موجودة.")

        media_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }
        media_type = media_types.get(photo_path.suffix.lower())
        if media_type is None:
            raise HTTPException(status_code=404, detail="صيغة صورة الموظف غير مدعومة.")
        return FileResponse(
            photo_path,
            media_type=media_type,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )


@app.get("/employees", response_class=HTMLResponse)
def employees_page(request: Request, admin: AdminAccount = Depends(get_current_admin)):
    with SessionLocal() as db:
        departments = db.scalars(select(Department).where(Department.active.is_(True)).order_by(Department.name)).all()
        branches = db.scalars(select(Branch).where(Branch.active.is_(True)).order_by(Branch.name)).all()
        all_departments = db.scalars(select(Department).order_by(Department.name)).all()
        all_branches = db.scalars(select(Branch).order_by(Branch.name)).all()
        employee_changes = db.scalars(select(EmployeeChange).order_by(EmployeeChange.effective_date.desc(), EmployeeChange.id.desc())).all()
        data_change_requests = db.scalars(select(EmployeeDataChangeRequest).order_by(EmployeeDataChangeRequest.recorded_at.desc())).all()
        employees = db.scalars(select(Employee).order_by(Employee.code)).all()
        return templates.TemplateResponse(
            request=request, name="employees.html",
            context={"departments": departments, "branches": branches, "all_departments": all_departments, "all_branches": all_branches, "employee_changes": employee_changes, "data_change_requests": data_change_requests, "employees": employees, "csrf_token": get_csrf_token(request)},
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
    probation_start_date: date | None = Form(None),
    probation_end_date: date | None = Form(None),
    basic_salary: Decimal = Form(...),
    daily_hours: Decimal = Form(...),
    fingerprint_id: str = Form(""),
    photo: UploadFile | None = File(None),
    admin: AdminAccount = Depends(get_current_admin),
):
    if basic_salary < 0 or daily_hours <= 0 or daily_hours > 24:
        return HTMLResponse("بيانات الراتب أو ساعات العمل غير صحيحة.", status_code=400)
    if probation_start_date and probation_end_date and probation_end_date < probation_start_date:
        raise HTTPException(status_code=400, detail="نهاية فترة الاختبار لا يمكن أن تسبق بدايتها.")
    full_name = full_name.strip()
    position = position.strip()
    national_id = national_id.strip() or None
    fingerprint_id = fingerprint_id.strip() or None
    if not full_name or not position:
        raise HTTPException(status_code=400, detail="اسم الموظف والمسمى الوظيفي مطلوبان.")
    with SessionLocal.begin() as db:
        department = db.get(Department, department_id)
        if department is None or not department.active:
            raise HTTPException(status_code=400, detail="القسم المحدد غير متاح.")
        branch = db.get(Branch, branch_id) if branch_id is not None else None
        if branch_id is not None and (branch is None or not branch.active or branch.department_id != department_id):
            raise HTTPException(status_code=400, detail="الفرع المحدد غير تابع للقسم المختار أو غير متاح.")
        for field_name, value in (("national_id", national_id), ("fingerprint_id", fingerprint_id)):
            if value is not None and db.scalar(
                select(Employee.id).where(getattr(Employee, field_name) == value).limit(1)
            ) is not None:
                raise HTTPException(status_code=409, detail=f"القيمة المدخلة في {field_name} مستخدمة لموظف آخر.")
        photo_path = _store_employee_photo(photo)
        employee = Employee(
            code=allocate_employee_code(db), full_name=full_name,
            national_id=national_id, phone=phone.strip() or None,
            address=address.strip() or None, department_id=department_id,
            branch_id=branch_id, position=position, hire_date=hire_date,
            probation_start_date=probation_start_date, probation_end_date=probation_end_date,
            basic_salary=basic_salary, daily_hours=daily_hours,
            fingerprint_id=fingerprint_id, photo_path=photo_path, status="active",
        )
        db.add(employee)
        db.flush()
        db.add(EmployeeServicePeriod(employee_id=employee.id, start_date=hire_date, start_reason="Initial employment"))
        db.add(AuditLog(
            actor=admin.username, action="employee_created",
            entity_type="employee", entity_id=str(employee.id),
            new_value=f"code={employee.code}; full_name={employee.full_name}; department_id={department_id}; branch_id={branch_id}",
            reason="Initial employee record created",
        ))
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
    probation_start_date: date | None = Form(None),
    probation_end_date: date | None = Form(None),
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
    if probation_start_date and probation_end_date and probation_end_date < probation_start_date:
        raise HTTPException(status_code=400, detail="نهاية فترة الاختبار لا يمكن أن تسبق بدايتها.")

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
            "probation_start_date": probation_start_date,
            "probation_end_date": probation_end_date,
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





@app.post("/employees/{employee_id}/data-change-requests", dependencies=[Depends(csrf_protect)])
def create_employee_data_change_request(
    employee_id: int,
    field_name: str = Form(...),
    requested_value: str = Form(...),
    request_reason: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    requested_value = requested_value.strip()
    request_reason = request_reason.strip()
    if field_name not in EMPLOYEE_PHOTO_FIELDS:
        raise HTTPException(status_code=400, detail="نوع البيانات المطلوب تغييره غير مسموح.")
    if not requested_value or not request_reason:
        raise HTTPException(status_code=400, detail="القيمة المطلوبة وسبب الطلب مطلوبان.")
    with SessionLocal.begin() as db:
        employee = db.get(Employee, employee_id)
        if employee is None:
            raise HTTPException(status_code=404, detail="الموظف غير موجود.")
        db.add(EmployeeDataChangeRequest(
            employee_id=employee_id, field_name=field_name,
            requested_value=requested_value, request_reason=request_reason,
            status="pending",
        ))
        db.add(AuditLog(
            actor=admin.username, action="employee_data_change_requested",
            entity_type="employee", entity_id=str(employee_id),
            new_value=f"{field_name}={requested_value}", reason=request_reason,
        ))
    return RedirectResponse("/employees", status_code=303)


@app.post("/employee-data-change-requests/{request_id}/review", dependencies=[Depends(csrf_protect)])
def review_employee_data_change_request(
    request_id: int,
    decision: str = Form(...),
    decision_reason: str = Form(...),
    effective_date: date | None = Form(None),
    admin: AdminAccount = Depends(get_current_admin),
):
    decision_reason = decision_reason.strip()
    if decision not in {"apply", "reject"} or not decision_reason:
        raise HTTPException(status_code=400, detail="قرار المراجعة وسببه مطلوبان.")
    with SessionLocal.begin() as db:
        change_request = db.get(EmployeeDataChangeRequest, request_id)
        if change_request is None:
            raise HTTPException(status_code=404, detail="طلب التعديل غير موجود.")
        if change_request.status != "pending":
            raise HTTPException(status_code=409, detail="تمت مراجعة هذا الطلب من قبل.")
        employee = db.get(Employee, change_request.employee_id)
        if employee is None:
            raise HTTPException(status_code=404, detail="الموظف المرتبط بالطلب غير موجود.")
        old_value = None
        new_value = None
        if decision == "apply":
            if effective_date is None:
                raise HTTPException(status_code=400, detail="تاريخ سريان التعديل مطلوب عند الموافقة.")
            if change_request.field_name not in EMPLOYEE_PHOTO_FIELDS:
                raise HTTPException(status_code=400, detail="حقل التعديل غير مسموح.")
            old_value = getattr(employee, change_request.field_name)
            new_value = change_request.requested_value
            if change_request.field_name == "national_id":
                duplicate = db.scalar(select(Employee.id).where(
                    Employee.national_id == new_value, Employee.id != employee.id
                ).limit(1))
                if duplicate is not None:
                    raise HTTPException(status_code=409, detail="الرقم القومي مستخدم لموظف آخر.")
            setattr(employee, change_request.field_name, new_value)
            db.add(EmployeeChange(
                employee_id=employee.id, field_name=change_request.field_name,
                old_value=str(old_value) if old_value is not None else None,
                new_value=new_value, effective_date=effective_date,
                reason=f"Approved request #{request_id}: {decision_reason}",
            ))
            change_request.status = "applied"
            change_request.effective_date = effective_date
        else:
            change_request.status = "rejected"
        change_request.decision_reason = decision_reason
        change_request.reviewed_at = datetime.now(timezone.utc)
        change_request.reviewed_by = admin.username
        db.add(AuditLog(
            actor=admin.username, action=f"employee_data_change_{change_request.status}",
            entity_type="employee_data_change_request", entity_id=str(request_id),
            old_value=str(old_value) if old_value is not None else None,
            new_value=new_value, reason=decision_reason,
        ))
    return RedirectResponse("/employees", status_code=303)


@app.post("/employees/{employee_id}/transfer", dependencies=[Depends(csrf_protect)])
def transfer_employee(
    employee_id: int,
    department_id: int = Form(...),
    branch_id: int | None = Form(None),
    effective_date: date = Form(...),
    reason: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    reason = reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="سبب النقل مطلوب.")
    with SessionLocal.begin() as db:
        employee = db.get(Employee, employee_id)
        if employee is None:
            raise HTTPException(status_code=404, detail="الموظف غير موجود.")
        if employee.status != "active":
            raise HTTPException(status_code=409, detail="لا يمكن نقل موظف غير نشط.")
        department = db.get(Department, department_id)
        if department is None or not department.active:
            raise HTTPException(status_code=400, detail="القسم الجديد غير متاح.")
        branch = db.get(Branch, branch_id) if branch_id is not None else None
        if branch_id is not None and (branch is None or not branch.active or branch.department_id != department_id):
            raise HTTPException(status_code=400, detail="الفرع الجديد غير تابع للقسم المختار أو غير متاح.")
        old_department_id, old_branch_id = employee.department_id, employee.branch_id
        if old_department_id == department_id and old_branch_id == branch_id:
            raise HTTPException(status_code=409, detail="الموظف موجود بالفعل في القسم والفرع المحددين.")
        if effective_date < employee.hire_date:
            raise HTTPException(status_code=400, detail="تاريخ النقل لا يمكن أن يسبق تاريخ تعيين الموظف.")
        changes = [
            ("department_id", old_department_id, department_id),
            ("branch_id", old_branch_id, branch_id),
        ]
        employee.department_id = department_id
        employee.branch_id = branch_id
        for field_name, old_value, new_value in changes:
            if old_value != new_value:
                db.add(EmployeeChange(
                    employee_id=employee.id, field_name=field_name,
                    old_value=str(old_value) if old_value is not None else None,
                    new_value=str(new_value) if new_value is not None else None,
                    effective_date=effective_date, reason=reason,
                ))
        db.add(AuditLog(
            actor=admin.username, action="employee_transferred",
            entity_type="employee", entity_id=str(employee.id),
            old_value=f"department_id={old_department_id}; branch_id={old_branch_id}",
            new_value=f"department_id={department_id}; branch_id={branch_id}",
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
