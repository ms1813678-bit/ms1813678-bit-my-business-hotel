import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
import secrets
import time as time_module

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from PIL import Image, UnidentifiedImageError
from starlette.middleware.sessions import SessionMiddleware

from app.auth import authenticate
from app.config import settings
from app.csrf import csrf_protect, get_csrf_token
from app.db import SessionLocal, engine, get_db
from app.dependencies import get_current_admin
from app.employee_codes import allocate_employee_code
from app.leave_workflow import add_balance_entry, cancel_leave, confirm_leave, leave_balance
from app.advance_workflow import approve_advance, create_advance, mark_advance_paid, reject_advance
from app.models import AdvanceInstallment
from app.staff_actions_workflow import approve_staff_action, cancel_staff_action, create_staff_action, record_employee_acknowledgement
from app.reports import build_hr_pdf, build_hr_workbook
from app.backup_workflow import create_backup, get_backup_root, restore_backup, save_backup_root
from app.payroll_workflow import (
    add_payroll_payment,
    approve_payroll_run,
    close_payroll_run,
    create_payroll_run,
    payment_totals,
    reopen_payroll_run,
    update_payroll_item,
)
from app.models import AdvanceRequest, BackupRun, SystemSetting, StaffAction, PayrollRun, PayrollItem, PayrollPayment, AdminAccount, AttendanceDailyRecord, AttendanceImportBatch, AttendanceImportIssue, AttendancePunch, AttendanceMonth, AuditLog, Branch, Department, Employee, EmployeeChange, EmployeeDataChangeRequest, EmployeeServicePeriod, LeaveBalanceTransaction, LeaveRecord, LeaveType
from app.security_roster_import import parse_security_roster_xlsx
from app.attendance_import import parse_attendance_workbook, normalize as normalize_attendance_alias
from app.attendance_workflow import persist_attendance_import, prepare_and_persist_monthly_reconciliation, approve_attendance_day, approve_attendance_month, resolve_unknown_attendance_employee, summarize_attendance_month_coverage
from app.security_roster_workflow import prepare_security_roster, confirm_security_roster_day

SECURITY_ROSTER_MAX_BYTES = 5 * 1024 * 1024
SECURITY_ROSTER_STAGING_DIR = Path("var") / "security_roster_staging"
SECURITY_ROSTER_STAGING_TTL_SECONDS = 60 * 60
SECURITY_ROSTER_CLEANUP_INTERVAL_SECONDS = 5 * 60


def _cleanup_expired_security_roster_files(now: float | None = None) -> int:
    """Delete expired staged workbooks independently of upload/preview actions."""
    if not SECURITY_ROSTER_STAGING_DIR.exists():
        return 0
    current_time = time_module.time() if now is None else now
    removed = 0
    for candidate in SECURITY_ROSTER_STAGING_DIR.glob("*.xlsx"):
        try:
            if current_time - candidate.stat().st_mtime > SECURITY_ROSTER_STAGING_TTL_SECONDS:
                candidate.unlink(missing_ok=True)
                removed += 1
        except FileNotFoundError:
            continue
        except OSError:
            # A transient filesystem problem must not stop the cleanup loop.
            continue
    return removed


async def _security_roster_cleanup_loop() -> None:
    while True:
        _cleanup_expired_security_roster_files()
        await asyncio.sleep(SECURITY_ROSTER_CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def _app_lifespan(application):
    cleanup_task = asyncio.create_task(_security_roster_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


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


app = FastAPI(title=settings.app_name, docs_url=None if settings.app_env == "production" else "/docs", lifespan=_app_lifespan)
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
        staged_token = request.session.get("security_roster_stage")
        if isinstance(staged_token, str):
            _clear_staged_security_roster(staged_token, request)
        request.session.clear()
        request.session["admin_username"] = admin.username
        request.session["admin_session_version"] = admin.session_version
        get_csrf_token(request)
    return RedirectResponse("/", status_code=303)


@app.post("/logout", dependencies=[Depends(csrf_protect)])
def logout(request: Request):
    staged_token = request.session.get("security_roster_stage")
    if isinstance(staged_token, str):
        _clear_staged_security_roster(staged_token, request)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, admin: AdminAccount = Depends(get_current_admin)):
    with SessionLocal() as db:
        metrics = {
            "active_employees": db.scalar(
                select(func.count(Employee.id)).where(Employee.status == "active")
            ) or 0,
            "former_employees": db.scalar(
                select(func.count(Employee.id)).where(Employee.status != "active")
            ) or 0,
            "attendance_review": db.scalar(
                select(func.count(AttendanceDailyRecord.id)).where(
                    AttendanceDailyRecord.status.in_(["needs_review", "ready_for_review"])
                )
            ) or 0,
            "leave_drafts": db.scalar(
                select(func.count(LeaveRecord.id)).where(LeaveRecord.status == "draft")
            ) or 0,
            "advance_pending": db.scalar(
                select(func.count(AdvanceRequest.id)).where(
                    AdvanceRequest.status.in_(["pending", "approved"])
                )
            ) or 0,
            "staff_action_drafts": db.scalar(
                select(func.count(StaffAction.id)).where(StaffAction.status == "draft")
            ) or 0,
            "payroll_drafts": db.scalar(
                select(func.count(PayrollRun.id)).where(
                    PayrollRun.status.in_(["draft", "reopened"])
                )
            ) or 0,
        }
        return templates.TemplateResponse(
            request=request, name="dashboard.html",
            context={
                "app_name": settings.app_name,
                "admin": admin.username,
                "csrf_token": get_csrf_token(request),
                "metrics": metrics,
            },
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
    _cleanup_expired_security_roster_files(now)
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


def _attendance_employee_aliases(db):
    employees = db.scalars(select(Employee).order_by(Employee.id)).all()
    aliases = {}
    owners = {}
    for employee in employees:
        for value in (employee.code, employee.fingerprint_id, employee.full_name):
            if not value:
                continue
            key = normalize_attendance_alias(value)
            owner = owners.get(key)
            if owner is not None and owner != employee.id:
                raise ValueError("employee_identifiers_are_ambiguous_for_attendance_import")
            owners[key] = employee.id
            aliases[str(value)] = employee.id
    if not aliases:
        raise ValueError("no_employee_identifiers_configured")
    return aliases


def _read_attendance_upload(upload: UploadFile) -> bytes:
    filename = (upload.filename or "").strip()
    if Path(filename).suffix.casefold() not in {".xls", ".xlsx"}:
        raise HTTPException(status_code=400, detail="يرجى رفع ملف Excel بصيغة .xls أو .xlsx.")
    raw = upload.file.read(SECURITY_ROSTER_MAX_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="الملف فارغ.")
    if len(raw) > SECURITY_ROSTER_MAX_BYTES:
        raise HTTPException(status_code=413, detail="حجم ملف الحضور يجب ألا يتجاوز 5 ميجابايت.")
    return raw


@app.get("/attendance/import/{batch_id}/download")
def attendance_import_download(
    batch_id: int,
    admin: AdminAccount = Depends(get_current_admin),
):
    """Return the exact original workbook retained with an attendance import batch."""
    with SessionLocal() as db:
        batch = db.get(AttendanceImportBatch, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="ملف الاستيراد غير موجود.")
        if not batch.original_file_bytes:
            raise HTTPException(
                status_code=410,
                detail="الملف الأصلي لهذه الدفعة لم يُحفظ في النسخة القديمة من النظام.",
            )
        suffix = Path(batch.original_filename).suffix.casefold()
        media_type = (
            "application/vnd.ms-excel" if suffix == ".xls"
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        from urllib.parse import quote
        filename = quote(Path(batch.original_filename).name)
        return Response(
            content=bytes(batch.original_file_bytes),
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
        )


@app.get("/attendance/import", response_class=HTMLResponse)
def attendance_import_page(
    request: Request,
    admin: AdminAccount = Depends(get_current_admin),
):
    return templates.TemplateResponse(
        request=request,
        name="attendance_import.html",
        context={"admin": admin.username, "csrf_token": get_csrf_token(request),
                 "error": None, "batch": None, "issues": []},
    )


@app.post("/attendance/import", response_class=HTMLResponse, dependencies=[Depends(csrf_protect)])
def attendance_import_upload(
    request: Request,
    source_type: str = Form(...),
    file: UploadFile = File(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    context = {
        "admin": admin.username, "csrf_token": get_csrf_token(request),
        "error": None, "batch": None, "issues": [],
    }
    try:
        if source_type not in {"program_report", "fingerprint"}:
            raise ValueError("unsupported_attendance_source_type")
        raw = _read_attendance_upload(file)
        with SessionLocal.begin() as db:
            aliases = _attendance_employee_aliases(db)
            parsed = parse_attendance_workbook(raw, file.filename or "attendance.xlsx", source_type, aliases)
            batch = persist_attendance_import(
                db, file_bytes=raw, filename=file.filename or "attendance.xlsx",
                parsed=parsed, actor=admin.username,
            )
            context.update({
                "batch": {
                    "id": batch.id, "source_type": batch.source_type,
                    "period_start": batch.period_start, "period_end": batch.period_end,
                    "row_count": batch.row_count, "issue_count": batch.issue_count,
                    "status": batch.status,
                },
                "issues": parsed["issues"],
            })
    except (ValueError, KeyError) as exc:
        context["error"] = str(exc)
        return templates.TemplateResponse(
            request=request, name="attendance_import.html", context=context, status_code=400
        )
    return templates.TemplateResponse(
        request=request, name="attendance_import.html", context=context
    )


def _attendance_import_batch_options():
    with SessionLocal() as db:
        return db.scalars(
            select(AttendanceImportBatch)
            .where(AttendanceImportBatch.status == "parsed")
            .order_by(AttendanceImportBatch.created_at.desc(), AttendanceImportBatch.id.desc())
            .limit(50)
        ).all()


@app.get("/attendance/import/{batch_id}/issues", response_class=HTMLResponse)
def attendance_import_issues_page(
    request: Request,
    batch_id: int,
    admin: AdminAccount = Depends(get_current_admin),
):
    with SessionLocal() as db:
        batch = db.get(AttendanceImportBatch, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="دفعة الاستيراد غير موجودة.")
        issues = db.scalars(
            select(AttendanceImportIssue)
            .where(AttendanceImportIssue.import_batch_id == batch_id)
            .order_by(AttendanceImportIssue.source_row, AttendanceImportIssue.id)
        ).all()
        employees = db.scalars(select(Employee).order_by(Employee.full_name, Employee.id)).all()
    return templates.TemplateResponse(
        request=request,
        name="attendance_import_issues.html",
        context={
            "admin": admin.username, "csrf_token": get_csrf_token(request),
            "batch": batch, "issues": issues, "employees": employees, "error": None,
        },
    )


@app.post(
    "/attendance/import/{batch_id}/issues/{issue_id}/resolve",
    response_class=HTMLResponse,
    dependencies=[Depends(csrf_protect)],
)
def attendance_import_issue_resolve(
    request: Request,
    batch_id: int,
    issue_id: int,
    employee_id: int = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal.begin() as db:
            issue = db.get(AttendanceImportIssue, issue_id)
            if issue is None or issue.import_batch_id != batch_id:
                raise ValueError("attendance_import_issue_not_found")
            resolve_unknown_attendance_employee(
                db, issue_id=issue_id, employee_id=employee_id, actor=admin.username
            )
    except ValueError as exc:
        with SessionLocal() as db:
            batch = db.get(AttendanceImportBatch, batch_id)
            issues = db.scalars(
                select(AttendanceImportIssue)
                .where(AttendanceImportIssue.import_batch_id == batch_id)
                .order_by(AttendanceImportIssue.source_row, AttendanceImportIssue.id)
            ).all() if batch else []
            employees = db.scalars(select(Employee).order_by(Employee.full_name, Employee.id)).all()
        return templates.TemplateResponse(
            request=request,
            name="attendance_import_issues.html",
            context={
                "admin": admin.username, "csrf_token": get_csrf_token(request),
                "batch": batch, "issues": issues, "employees": employees,
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse(f"/attendance/import/{batch_id}/issues", status_code=303)


@app.get("/attendance/reconcile", response_class=HTMLResponse)
def attendance_reconcile_page(
    request: Request,
    admin: AdminAccount = Depends(get_current_admin),
):
    batches = _attendance_import_batch_options()
    return templates.TemplateResponse(
        request=request,
        name="attendance_reconcile.html",
        context={
            "admin": admin.username, "csrf_token": get_csrf_token(request),
            "batches": batches, "error": None, "summary": None,
        },
    )


@app.post("/attendance/reconcile", response_class=HTMLResponse, dependencies=[Depends(csrf_protect)])
def attendance_reconcile_upload(
    request: Request,
    program_batch_id: int = Form(...),
    fingerprint_batch_id: int = Form(...),
    period_start: str = Form(...),
    period_end: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    context = {
        "admin": admin.username, "csrf_token": get_csrf_token(request),
        "batches": _attendance_import_batch_options(), "error": None, "summary": None,
    }
    try:
        selected_start = date.fromisoformat(period_start)
        selected_end = date.fromisoformat(period_end)
        with SessionLocal.begin() as db:
            summary = prepare_and_persist_monthly_reconciliation(
                db,
                program_batch_id=program_batch_id,
                fingerprint_batch_id=fingerprint_batch_id,
                period_start=selected_start,
                period_end=selected_end,
                actor=admin.username,
            )
        context["summary"] = summary
    except (ValueError, KeyError) as exc:
        context["error"] = str(exc)
        return templates.TemplateResponse(
            request=request, name="attendance_reconcile.html",
            context=context, status_code=400,
        )
    return templates.TemplateResponse(
        request=request, name="attendance_reconcile.html", context=context
    )


@app.get("/attendance/review", response_class=HTMLResponse)
def attendance_review_page(
    request: Request,
    page: int = 1,
    admin: AdminAccount = Depends(get_current_admin),
):
    page_size = 100
    page = max(1, page)
    pending_filter = AttendanceDailyRecord.status.in_(["ready_for_review", "needs_review"])
    with SessionLocal() as db:
        total_records = db.scalar(
            select(func.count()).select_from(AttendanceDailyRecord).where(pending_filter)
        ) or 0
        total_pages = max(1, (total_records + page_size - 1) // page_size)
        page = min(page, total_pages)
        rows = db.execute(
            select(AttendanceDailyRecord, Employee)
            .join(Employee, Employee.id == AttendanceDailyRecord.employee_id)
            .where(pending_filter)
            .order_by(AttendanceDailyRecord.work_date, Employee.full_name, AttendanceDailyRecord.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        records = []
        for record, employee in rows:
            suggested_in = record.program_clock_in or record.first_clock_in
            suggested_out = record.program_clock_out or record.last_clock_out
            if (
                suggested_in is not None and suggested_out is not None
                and suggested_out <= suggested_in
                and record.scheduled_start_at is not None
                and record.scheduled_end_at is not None
                and record.scheduled_end_at.date() > record.scheduled_start_at.date()
                and suggested_out.date() == record.work_date
            ):
                suggested_out += timedelta(days=1)
            records.append({
                "id": record.id, "employee": employee.full_name,
                "employee_code": employee.code, "work_date": record.work_date,
                "scheduled_start_at": record.scheduled_start_at,
                "scheduled_end_at": record.scheduled_end_at,
                "program_clock_in": record.program_clock_in,
                "program_clock_out": record.program_clock_out,
                "fingerprint_in": record.first_clock_in,
                "fingerprint_out": record.last_clock_out,
                "suggested_clock_in": suggested_in,
                "suggested_clock_out": suggested_out,
                "worked_minutes": record.worked_minutes,
                "status": record.status, "review_notes": record.review_notes,
            })
    return templates.TemplateResponse(
        request=request, name="attendance_review.html",
        context={"admin": admin.username, "csrf_token": get_csrf_token(request),
                 "records": records, "error": None, "page": page,
                 "total_pages": total_pages, "total_records": total_records,
                 "page_size": page_size},
    )


@app.post("/attendance/{record_id}/approve", response_class=HTMLResponse, dependencies=[Depends(csrf_protect)])
def attendance_approve(
    record_id: int,
    request: Request,
    clock_in: str = Form(...),
    clock_out: str = Form(...),
    reason: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        approved_in = datetime.fromisoformat(clock_in)
        approved_out = datetime.fromisoformat(clock_out)
        with SessionLocal.begin() as db:
            approve_attendance_day(
                db, record_id=record_id, clock_in=approved_in,
                clock_out=approved_out, actor=admin.username, reason=reason,
            )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/attendance/review", status_code=303)


@app.get("/attendance/months", response_class=HTMLResponse)
def attendance_months_page(
    request: Request,
    admin: AdminAccount = Depends(get_current_admin),
):
    with SessionLocal() as db:
        months = db.scalars(
            select(AttendanceMonth).order_by(
                AttendanceMonth.period_start.desc(), AttendanceMonth.id.desc()
            )
        ).all()
        summaries = []
        for month in months:
            grouped = db.execute(
                select(AttendanceDailyRecord.status, func.count())
                .where(
                    AttendanceDailyRecord.work_date >= month.period_start,
                    AttendanceDailyRecord.work_date <= month.period_end,
                    AttendanceDailyRecord.program_import_batch_id == month.program_import_batch_id,
                    AttendanceDailyRecord.fingerprint_import_batch_id == month.fingerprint_import_batch_id,
                )
                .group_by(AttendanceDailyRecord.status)
            ).all()
            counts = {status: count for status, count in grouped}
            program = db.get(AttendanceImportBatch, month.program_import_batch_id) if month.program_import_batch_id else None
            fingerprint = db.get(AttendanceImportBatch, month.fingerprint_import_batch_id) if month.fingerprint_import_batch_id else None
            coverage = summarize_attendance_month_coverage(db, month)
            summaries.append({
                "id": month.id, "period_start": month.period_start,
                "period_end": month.period_end, "status": month.status,
                "total": sum(counts.values()),
                "approved": counts.get("approved", 0),
                "pending": sum(value for key, value in counts.items() if key != "approved"),
                "program_issues": program.issue_count if program else None,
                "fingerprint_issues": fingerprint.issue_count if fingerprint else None,
                "unrepresented_employee_days": coverage["unrepresented_employee_days"],
                "coverage_examples": coverage["coverage_examples"],
                "approved_by": month.approved_by,
            })
    return templates.TemplateResponse(
        request=request, name="attendance_months.html",
        context={"admin": admin.username, "csrf_token": get_csrf_token(request),
                 "months": summaries, "error": None},
    )


@app.post("/attendance/months/{month_id}/approve", response_class=HTMLResponse, dependencies=[Depends(csrf_protect)])
def attendance_month_approve(
    month_id: int,
    request: Request,
    reason: str = Form(...),
    coverage_confirmed: bool = Form(False),
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal.begin() as db:
            approve_attendance_month(
                db, month_id=month_id, actor=admin.username,
                reason=reason, coverage_confirmed=coverage_confirmed,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/attendance/months", status_code=303)


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
    reviewed: str = Form(""),
    admin: AdminAccount = Depends(get_current_admin),
):
    from datetime import date
    if reviewed != "yes":
        raise HTTPException(status_code=400, detail="يجب تأكيد مراجعة ورديات اليوم قبل الاعتماد.")
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
        # Keep the staged workbook available so the administrator can approve
        # each clean date independently; TTL cleanup removes it after one hour.
        # Rebuild the preview after commit so the remaining dates can be
        # approved from the same workbook without uploading it again.
        from app.models import SecurityShiftAssignment
        with SessionLocal() as db:
            aliases = _security_roster_aliases(db)
            refreshed = parse_security_roster_xlsx(raw, aliases)
            refreshed_preview = prepare_security_roster(db, refreshed)
            approved_dates = set(db.scalars(
                select(SecurityShiftAssignment.work_date).where(
                    SecurityShiftAssignment.work_date.in_(
                        [day["work_date"] for day in refreshed_preview["days"]]
                    )
                )
            ).all())
            for day in refreshed_preview["days"]:
                if day["work_date"] in approved_dates:
                    day["can_confirm"] = False
                    day["already_approved"] = True
        return templates.TemplateResponse(
            request=request,
            name="security_roster.html",
            context={
                "admin": admin.username,
                "csrf_token": get_csrf_token(request),
                "days": refreshed_preview["days"],
                "issues": refreshed_preview["import_issues"],
                "error": None,
                "success": f"تم اعتماد جدول يوم {selected_date.isoformat()} بنجاح.",
                "filename": "ملف جدول الأمن",
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


@app.get("/leave", response_class=HTMLResponse)
def leave_page(request: Request, admin: AdminAccount = Depends(get_current_admin)):
    with SessionLocal() as db:
        employees = db.scalars(
            select(Employee).where(Employee.status == "active").order_by(Employee.code)
        ).all()
        leave_types = db.scalars(
            select(LeaveType).where(LeaveType.active.is_(True)).order_by(LeaveType.id)
        ).all()
        records = db.scalars(
            select(LeaveRecord).order_by(LeaveRecord.created_at.desc(), LeaveRecord.id.desc())
        ).all()
        all_employees = db.scalars(select(Employee).order_by(Employee.code)).all()
        transactions = db.scalars(
            select(LeaveBalanceTransaction).order_by(
                LeaveBalanceTransaction.created_at.desc(),
                LeaveBalanceTransaction.id.desc(),
            ).limit(100)
        ).all()
        balance_rows = db.execute(
            select(
                LeaveBalanceTransaction.employee_id,
                LeaveBalanceTransaction.leave_type_id,
                func.sum(LeaveBalanceTransaction.days_delta),
            ).group_by(
                LeaveBalanceTransaction.employee_id,
                LeaveBalanceTransaction.leave_type_id,
            )
        ).all()
        balances = {
            f"{employee_id}:{leave_type_id}": Decimal(str(total or 0))
            for employee_id, leave_type_id, total in balance_rows
        }
        return templates.TemplateResponse(
            request=request,
            name="leave.html",
            context={
                "employees": employees,
                "all_employees": all_employees,
                "leave_types": leave_types,
                "records": records,
                "employee_names": {employee.id: employee.full_name for employee in all_employees},
                "employee_codes": {employee.id: employee.code for employee in all_employees},
                "type_names": {item.id: item.name_ar for item in db.scalars(select(LeaveType)).all()},
                "balances": balances,
                "transactions": transactions,
                "csrf_token": get_csrf_token(request),
                "error": request.query_params.get("error"),
            },
        )


@app.post("/leave/types", dependencies=[Depends(csrf_protect)])
def create_leave_type(
    code: str = Form(...),
    name_ar: str = Form(...),
    name_en: str = Form(...),
    is_paid: str | None = Form(None),
    tracks_balance: str | None = Form(None),
    admin: AdminAccount = Depends(get_current_admin),
):
    normalized_code = code.strip().lower()
    if not name_ar.strip() or not name_en.strip():
        return RedirectResponse("/leave?error=type", status_code=303)
    if not normalized_code or not normalized_code.replace("_", "").isalnum():
        return RedirectResponse("/leave?error=type", status_code=303)
    with SessionLocal.begin() as db:
        if db.scalar(select(LeaveType.id).where(LeaveType.code == normalized_code)):
            return RedirectResponse("/leave?error=type", status_code=303)
        item = LeaveType(
            code=normalized_code,
            name_ar=name_ar.strip(),
            name_en=name_en.strip(),
            is_paid=is_paid == "yes",
            tracks_balance=tracks_balance == "yes",
            active=True,
        )
        db.add(item)
        db.flush()
        db.add(AuditLog(
            actor=admin.username, action="leave_type_created",
            entity_type="leave_type", entity_id=str(item.id),
            new_value=f"{normalized_code}|{name_ar.strip()}|{name_en.strip()}",
            reason="Created by HR administrator",
        ))
    return RedirectResponse("/leave", status_code=303)


@app.post("/leave/balances", dependencies=[Depends(csrf_protect)])
def record_leave_balance(
    employee_id: int = Form(...),
    leave_type_id: int = Form(...),
    transaction_type: str = Form(...),
    days_delta: Decimal = Form(...),
    reason: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    if transaction_type not in {"opening", "adjustment"} or not reason.strip():
        return RedirectResponse("/leave?error=balance", status_code=303)
    try:
        with SessionLocal.begin() as db:
            employee = db.get(Employee, employee_id)
            leave_type = db.get(LeaveType, leave_type_id)
            if employee is None or employee.status != "active" or leave_type is None:
                raise ValueError("Invalid employee or leave type.")
            add_balance_entry(
                db, employee_id=employee_id, leave_type=leave_type,
                transaction_type=transaction_type, days_delta=days_delta,
                reason=reason, actor=admin.username,
            )
    except (ValueError, SQLAlchemyError):
        return RedirectResponse("/leave?error=balance", status_code=303)
    return RedirectResponse("/leave", status_code=303)


@app.post("/leave/records", dependencies=[Depends(csrf_protect)])
def create_leave_record(
    employee_id: int = Form(...),
    leave_type_id: int = Form(...),
    start_date: date = Form(...),
    end_date: date = Form(...),
    charged_days: Decimal = Form(...),
    reason: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    if end_date < start_date or charged_days <= 0 or not reason.strip():
        return RedirectResponse("/leave?error=dates", status_code=303)
    calendar_days = (end_date - start_date).days + 1
    if charged_days > Decimal(calendar_days):
        return RedirectResponse("/leave?error=days", status_code=303)
    with SessionLocal.begin() as db:
        employee = db.get(Employee, employee_id)
        leave_type = db.get(LeaveType, leave_type_id)
        if employee is None or employee.status != "active" or leave_type is None or not leave_type.active:
            return RedirectResponse("/leave?error=employee", status_code=303)
        overlap = db.scalar(
            select(LeaveRecord.id).where(
                LeaveRecord.employee_id == employee_id,
                LeaveRecord.status != "cancelled",
                LeaveRecord.start_date <= end_date,
                LeaveRecord.end_date >= start_date,
            ).limit(1)
        )
        if overlap is not None:
            return RedirectResponse("/leave?error=overlap", status_code=303)
        # Planned leave needs at least 72 hours' notice. Sick and emergency leave
        # are exceptions, but the administrator must still enter a reason.
        starts_at = datetime.combine(start_date, datetime.min.time())
        if leave_type.code not in {"sick", "casual"} and starts_at < datetime.now() + timedelta(hours=72):
            return RedirectResponse("/leave?error=notice", status_code=303)
        record = LeaveRecord(
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            start_date=start_date,
            end_date=end_date,
            calendar_days=calendar_days,
            charged_days=charged_days,
            reason=reason.strip(),
            status="draft",
            created_by=admin.username,
        )
        db.add(record)
        db.flush()
        db.add(AuditLog(
            actor=admin.username, action="leave_record_created",
            entity_type="leave_record", entity_id=str(record.id),
            new_value=f"{start_date.isoformat()}|{end_date.isoformat()}|{charged_days}",
            reason=reason.strip(),
        ))
    return RedirectResponse("/leave", status_code=303)


@app.post("/leave/records/{record_id}/confirm", dependencies=[Depends(csrf_protect)])
def confirm_leave_record(
    record_id: int,
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal.begin() as db:
            record = db.scalar(
                select(LeaveRecord)
                .where(LeaveRecord.id == record_id)
                .with_for_update()
            )
            if record is None:
                return RedirectResponse("/leave?error=record", status_code=303)
            # Serialize balance-consuming approvals for the same employee.
            db.scalar(
                select(Employee)
                .where(Employee.id == record.employee_id)
                .with_for_update()
            )
            confirm_leave(db, record, admin.username)
    except (ValueError, SQLAlchemyError):
        return RedirectResponse("/leave?error=confirm", status_code=303)
    return RedirectResponse("/leave", status_code=303)


@app.post("/leave/records/{record_id}/cancel", dependencies=[Depends(csrf_protect)])
def cancel_leave_record(
    record_id: int,
    reason: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    if not reason.strip():
        return RedirectResponse("/leave?error=cancel", status_code=303)
    try:
        with SessionLocal.begin() as db:
            record = db.get(LeaveRecord, record_id)
            if record is None:
                return RedirectResponse("/leave?error=record", status_code=303)
            cancel_leave(db, record, admin.username, reason)
    except (ValueError, SQLAlchemyError):
        return RedirectResponse("/leave?error=cancel", status_code=303)
    return RedirectResponse("/leave", status_code=303)


@app.get("/advances", response_class=HTMLResponse)
def advances_page(request: Request, admin: AdminAccount = Depends(get_current_admin)):
    with SessionLocal() as db:
        employees = db.scalars(
            select(Employee).where(Employee.status == "active").order_by(Employee.code)
        ).all()
        all_employees = db.scalars(select(Employee).order_by(Employee.code)).all()
        requests = db.scalars(
            select(AdvanceRequest).order_by(
                AdvanceRequest.request_date.desc(), AdvanceRequest.id.desc()
            )
        ).all()
        advance_summary = {}
        for employee in employees:
            employee_requests = [item for item in requests if item.employee_id == employee.id]
            regular_outstanding = Decimal("0.00")
            installment_outstanding = Decimal("0.00")
            deducted = Decimal("0.00")
            for item in employee_requests:
                applied = db.scalar(
                    select(func.coalesce(func.sum(AdvanceInstallment.amount), 0)).where(
                        AdvanceInstallment.advance_request_id == item.id,
                        AdvanceInstallment.status == "applied",
                    )
                ) or Decimal("0.00")
                if item.status in {"pending", "approved", "paid"}:
                    remaining = Decimal(item.amount) - Decimal(applied)
                else:
                    remaining = Decimal("0.00")
                if item.advance_type == "regular":
                    regular_outstanding += max(Decimal("0.00"), remaining)
                else:
                    installment_outstanding += max(Decimal("0.00"), remaining)
                deducted += Decimal(applied)
            total = sum(
                (Decimal(item.amount) for item in employee_requests),
                Decimal("0.00"),
            )
            cap = Decimal(employee.basic_salary) * Decimal("0.25")
            advance_summary[employee.id] = {
                "latest": employee_requests[0] if employee_requests else None,
                "total": total,
                "deducted": deducted,
                "outstanding": regular_outstanding + installment_outstanding,
                "regular_outstanding": regular_outstanding,
                "installment_outstanding": installment_outstanding,
                "limit": cap,
                "available": max(Decimal("0.00"), cap - regular_outstanding),
            }
        return templates.TemplateResponse(
            request=request,
            name="advances.html",
            context={
                "employees": employees,
                "requests": requests,
                "advance_summary": advance_summary,
                "employee_names": {e.id: e.full_name for e in all_employees},
                "employee_codes": {e.id: e.code for e in all_employees},
                "today": date.today().isoformat(),
                "csrf_token": get_csrf_token(request),
                "error": request.query_params.get("error"),
            },
        )


@app.post("/advances", dependencies=[Depends(csrf_protect)])
def create_advance_request(
    employee_id: int = Form(...),
    request_date: date = Form(...),
    amount: Decimal = Form(...),
    advance_type: str = Form("regular"),
    reason: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal.begin() as db:
            create_advance(
                db,
                employee_id=employee_id,
                request_date=request_date,
                amount=amount,
                reason=reason,
                advance_type=advance_type,
                actor=admin.username,
            )
    except (ValueError, SQLAlchemyError):
        return RedirectResponse("/advances?error=request", status_code=303)
    return RedirectResponse("/advances", status_code=303)


@app.post("/advances/{request_id}/approve", dependencies=[Depends(csrf_protect)])
def approve_advance_request(
    request_id: int,
    installment_count: int = Form(1),
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal.begin() as db:
            request = db.scalar(
                select(AdvanceRequest)
                .where(AdvanceRequest.id == request_id)
                .with_for_update()
            )
            if request is None:
                return RedirectResponse("/advances?error=record", status_code=303)
            approve_advance(db, request, admin.username, installment_count=installment_count)
    except (ValueError, SQLAlchemyError):
        return RedirectResponse("/advances?error=approve", status_code=303)
    return RedirectResponse("/advances", status_code=303)


@app.post("/advances/{request_id}/reject", dependencies=[Depends(csrf_protect)])
def reject_advance_request(
    request_id: int,
    reason: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal.begin() as db:
            request = db.scalar(
                select(AdvanceRequest)
                .where(AdvanceRequest.id == request_id)
                .with_for_update()
            )
            if request is None:
                return RedirectResponse("/advances?error=record", status_code=303)
            reject_advance(db, request, admin.username, reason)
    except (ValueError, SQLAlchemyError):
        return RedirectResponse("/advances?error=reject", status_code=303)
    return RedirectResponse("/advances", status_code=303)


@app.post("/advances/{request_id}/paid", dependencies=[Depends(csrf_protect)])
def mark_advance_request_paid(
    request_id: int,
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal.begin() as db:
            request = db.scalar(
                select(AdvanceRequest)
                .where(AdvanceRequest.id == request_id)
                .with_for_update()
            )
            if request is None:
                return RedirectResponse("/advances?error=record", status_code=303)
            mark_advance_paid(db, request, admin.username)
    except (ValueError, SQLAlchemyError):
        return RedirectResponse("/advances?error=paid", status_code=303)
    return RedirectResponse("/advances", status_code=303)


@app.get("/staff-actions", response_class=HTMLResponse)
def staff_actions_page(request: Request, admin: AdminAccount = Depends(get_current_admin)):
    with SessionLocal() as db:
        employees = db.scalars(
            select(Employee).where(Employee.status == "active").order_by(Employee.code)
        ).all()
        all_employees = db.scalars(select(Employee).order_by(Employee.code)).all()
        actions = db.scalars(
            select(StaffAction).order_by(StaffAction.created_at.desc(), StaffAction.id.desc())
        ).all()
        return templates.TemplateResponse(
            request=request,
            name="staff_actions.html",
            context={
                "employees": employees,
                "actions": actions,
                "employee_names": {e.id: e.full_name for e in all_employees},
                "employee_codes": {e.id: e.code for e in all_employees},
                "csrf_token": get_csrf_token(request),
                "error": request.query_params.get("error"),
            },
        )


@app.post("/staff-actions", dependencies=[Depends(csrf_protect)])
def create_staff_action_route(
    employee_id: int = Form(...),
    kind: str = Form(...),
    category: str = Form(...),
    title: str = Form(...),
    is_financial: str | None = Form(None),
    amount: Decimal | None = Form(None),
    payroll_month: date | None = Form(None),
    reason: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal.begin() as db:
            create_staff_action(
                db,
                employee_id=employee_id,
                kind=kind,
                category=category,
                title=title,
                is_financial=is_financial == "yes",
                amount=amount,
                payroll_month=payroll_month,
                reason=reason,
                actor=admin.username,
            )
    except (ValueError, SQLAlchemyError):
        return RedirectResponse("/staff-actions?error=create", status_code=303)
    return RedirectResponse("/staff-actions", status_code=303)


@app.post("/staff-actions/{action_id}/approve", dependencies=[Depends(csrf_protect)])
def approve_staff_action_route(
    action_id: int,
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal.begin() as db:
            item = db.scalar(
                select(StaffAction)
                .where(StaffAction.id == action_id)
                .with_for_update()
            )
            if item is None:
                return RedirectResponse("/staff-actions?error=record", status_code=303)
            approve_staff_action(db, item, admin.username)
    except (ValueError, SQLAlchemyError):
        return RedirectResponse("/staff-actions?error=approve", status_code=303)
    return RedirectResponse("/staff-actions", status_code=303)


@app.post("/staff-actions/{action_id}/cancel", dependencies=[Depends(csrf_protect)])
def cancel_staff_action_route(
    action_id: int,
    reason: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal.begin() as db:
            item = db.scalar(
                select(StaffAction)
                .where(StaffAction.id == action_id)
                .with_for_update()
            )
            if item is None:
                return RedirectResponse("/staff-actions?error=record", status_code=303)
            cancel_staff_action(db, item, admin.username, reason)
    except (ValueError, SQLAlchemyError):
        return RedirectResponse("/staff-actions?error=cancel", status_code=303)
    return RedirectResponse("/staff-actions", status_code=303)


@app.post("/staff-actions/{action_id}/acknowledgement", dependencies=[Depends(csrf_protect)])
def staff_action_acknowledgement_route(
    action_id: int,
    status: str = Form(...),
    note: str = Form(""),
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal.begin() as db:
            item = db.scalar(
                select(StaffAction)
                .where(StaffAction.id == action_id)
                .with_for_update()
            )
            if item is None:
                return RedirectResponse("/staff-actions?error=record", status_code=303)
            record_employee_acknowledgement(
                db, item, admin.username, status, note
            )
    except (ValueError, SQLAlchemyError):
        return RedirectResponse("/staff-actions?error=ack", status_code=303)
    return RedirectResponse("/staff-actions", status_code=303)


@app.get("/payroll", response_class=HTMLResponse)
def payroll_page(request: Request, admin: AdminAccount = Depends(get_current_admin)):
    with SessionLocal() as db:
        runs = db.scalars(
            select(PayrollRun).order_by(PayrollRun.period_start.desc())
        ).all()
        return templates.TemplateResponse(
            request=request,
            name="payroll.html",
            context={
                "runs": runs,
                "csrf_token": get_csrf_token(request),
                "error": request.query_params.get("error"),
            },
        )


@app.post("/payroll", dependencies=[Depends(csrf_protect)])
def create_payroll_run_route(
    year: int = Form(...),
    month: int = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal.begin() as db:
            run = create_payroll_run(db, year=year, month=month, actor=admin.username)
            run_id = run.id
    except (ValueError, SQLAlchemyError):
        return RedirectResponse("/payroll?error=create", status_code=303)
    return RedirectResponse(f"/payroll/runs/{run_id}", status_code=303)


@app.get("/payroll/runs/{run_id}", response_class=HTMLResponse)
def payroll_run_page(
    run_id: int,
    request: Request,
    admin: AdminAccount = Depends(get_current_admin),
):
    with SessionLocal() as db:
        run = db.get(PayrollRun, run_id)
        if run is None:
            return RedirectResponse("/payroll?error=run", status_code=303)
        items = db.scalars(
            select(PayrollItem)
            .where(PayrollItem.payroll_run_id == run.id)
            .order_by(PayrollItem.employee_id)
        ).all()
        employee_ids = [item.employee_id for item in items]
        employees = db.scalars(
            select(Employee).where(Employee.id.in_(employee_ids or {-1}))
        ).all()
        employee_names = {e.id: e.full_name for e in employees}
        employee_codes = {e.id: e.code for e in employees}
        summaries = {item.id: payment_totals(db, item) for item in items}
        financials = {}
        for item in items:
            gross = (
                Decimal(item.base_salary)
                + Decimal(item.attendance_bonus)
                + Decimal(item.performance_bonus)
                + Decimal(item.occupancy_bonus)
                + Decimal(item.overtime_amount)
                + Decimal(item.other_bonus)
                + Decimal(item.staff_reward_amount)
            )
            deductions = (
                Decimal(item.absence_deduction)
                + Decimal(item.lateness_deduction)
                + Decimal(item.insurance_share)
                + Decimal(item.taxes)
                + Decimal(item.other_deductions)
                + Decimal(item.advance_deduction)
                + Decimal(item.staff_sanction_amount)
            )
            financials[item.id] = {"gross": gross, "deductions": deductions}
        payments = db.scalars(
            select(PayrollPayment)
            .where(PayrollPayment.payroll_item_id.in_([item.id for item in items] or {-1}))
            .order_by(PayrollPayment.paid_at.desc(), PayrollPayment.id.desc())
        ).all()
        return templates.TemplateResponse(
            request=request,
            name="payroll_run.html",
            context={
                "run": run,
                "items": items,
                "employee_names": employee_names,
                "employee_codes": employee_codes,
                "summaries": summaries,
                "financials": financials,
                "payments": payments,
                "csrf_token": get_csrf_token(request),
                "error": request.query_params.get("error"),
            },
        )


@app.post("/payroll/runs/{run_id}/approve", dependencies=[Depends(csrf_protect)])
def approve_payroll_run_route(
    run_id: int,
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal.begin() as db:
            run = db.scalar(
                select(PayrollRun).where(PayrollRun.id == run_id).with_for_update()
            )
            if run is None:
                return RedirectResponse("/payroll?error=run", status_code=303)
            approve_payroll_run(db, run, admin.username)
    except (ValueError, SQLAlchemyError):
        return RedirectResponse(f"/payroll/runs/{run_id}?error=approve", status_code=303)
    return RedirectResponse(f"/payroll/runs/{run_id}", status_code=303)


@app.post("/payroll/runs/{run_id}/close", dependencies=[Depends(csrf_protect)])
def close_payroll_run_route(
    run_id: int,
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal.begin() as db:
            run = db.scalar(
                select(PayrollRun).where(PayrollRun.id == run_id).with_for_update()
            )
            if run is None:
                return RedirectResponse("/payroll?error=run", status_code=303)
            close_payroll_run(db, run, admin.username)
    except (ValueError, SQLAlchemyError):
        return RedirectResponse(f"/payroll/runs/{run_id}?error=close", status_code=303)
    return RedirectResponse(f"/payroll/runs/{run_id}", status_code=303)


@app.post("/payroll/runs/{run_id}/reopen", dependencies=[Depends(csrf_protect)])
def reopen_payroll_run_route(
    run_id: int,
    reason: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal.begin() as db:
            run = db.scalar(
                select(PayrollRun).where(PayrollRun.id == run_id).with_for_update()
            )
            if run is None:
                return RedirectResponse("/payroll?error=run", status_code=303)
            reopen_payroll_run(db, run, admin.username, reason)
    except (ValueError, SQLAlchemyError):
        return RedirectResponse(f"/payroll/runs/{run_id}?error=reopen", status_code=303)
    return RedirectResponse(f"/payroll/runs/{run_id}", status_code=303)


@app.post("/payroll/items/{item_id}/update", dependencies=[Depends(csrf_protect)])
def update_payroll_item_route(
    item_id: int,
    attendance_bonus: Decimal = Form(Decimal("0")),
    performance_bonus: Decimal = Form(Decimal("0")),
    occupancy_bonus: Decimal = Form(Decimal("0")),
    overtime_hours: Decimal = Form(Decimal("0")),
    other_bonus: Decimal = Form(Decimal("0")),
    absence_days: Decimal = Form(Decimal("0")),
    lateness_deduction: Decimal = Form(Decimal("0")),
    insurance_share: Decimal = Form(Decimal("0")),
    taxes: Decimal = Form(Decimal("0")),
    other_deductions: Decimal = Form(Decimal("0")),
    note: str = Form(""),
    admin: AdminAccount = Depends(get_current_admin),
):
    run_id = None
    try:
        with SessionLocal.begin() as db:
            item = db.scalar(
                select(PayrollItem).where(PayrollItem.id == item_id).with_for_update()
            )
            if item is None:
                return RedirectResponse("/payroll?error=item", status_code=303)
            run_id = item.payroll_run_id
            update_payroll_item(
                db,
                item,
                {
                    "attendance_bonus": attendance_bonus,
                    "performance_bonus": performance_bonus,
                    "occupancy_bonus": occupancy_bonus,
                    "overtime_hours": overtime_hours,
                    "other_bonus": other_bonus,
                    "absence_days": absence_days,
                    "lateness_deduction": lateness_deduction,
                    "insurance_share": insurance_share,
                    "taxes": taxes,
                    "other_deductions": other_deductions,
                    "note": note,
                },
                admin.username,
            )
    except (ValueError, SQLAlchemyError):
        return RedirectResponse(f"/payroll/runs/{run_id}?error=item", status_code=303)
    return RedirectResponse(f"/payroll/runs/{run_id}", status_code=303)


@app.post("/payroll/items/{item_id}/payments", dependencies=[Depends(csrf_protect)])
def add_payroll_payment_route(
    item_id: int,
    amount: Decimal = Form(...),
    paid_date: date = Form(...),
    method: str = Form(...),
    reference: str = Form(""),
    admin: AdminAccount = Depends(get_current_admin),
):
    run_id = None
    try:
        with SessionLocal.begin() as db:
            item = db.scalar(
                select(PayrollItem).where(PayrollItem.id == item_id).with_for_update()
            )
            if item is None:
                return RedirectResponse("/payroll?error=item", status_code=303)
            run_id = item.payroll_run_id
            add_payroll_payment(
                db,
                item,
                amount=amount,
                paid_date=paid_date,
                method=method,
                reference=reference,
                actor=admin.username,
            )
    except (ValueError, SQLAlchemyError):
        return RedirectResponse(f"/payroll/runs/{run_id}?error=payment", status_code=303)
    return RedirectResponse(f"/payroll/runs/{run_id}", status_code=303)


@app.get("/reports", response_class=HTMLResponse)
def reports_page(
    request: Request,
    admin: AdminAccount = Depends(get_current_admin),
):
    today = date.today()
    default_start = date(today.year, today.month, 1)
    return templates.TemplateResponse(
        request=request,
        name="reports.html",
        context={
            "start_date": request.query_params.get("start_date", default_start.isoformat()),
            "end_date": request.query_params.get("end_date", today.isoformat()),
            "error": request.query_params.get("error"),
        },
    )


@app.get("/reports/export.xlsx")
def export_hr_reports(
    start_date: date,
    end_date: date,
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal() as db:
            content = build_hr_workbook(db, start_date, end_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="تعذر إنشاء التقرير بسبب خطأ في قاعدة البيانات.")
    filename = f"MY_HR_Report_{start_date.isoformat()}_{end_date.isoformat()}.xlsx"
    from urllib.parse import quote
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )



@app.get("/reports/export.pdf")
def export_hr_reports_pdf(
    start_date: date,
    end_date: date,
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal() as db:
            content = build_hr_pdf(db, start_date, end_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="تعذر إنشاء التقرير بسبب خطأ في قاعدة البيانات.")
    filename = f"MY_HR_Report_{start_date.isoformat()}_{end_date.isoformat()}.pdf"
    from urllib.parse import quote
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )

@app.get("/settings/backup", response_class=HTMLResponse)
def backup_settings_page(
    request: Request,
    admin: AdminAccount = Depends(get_current_admin),
):
    with SessionLocal() as db:
        root = get_backup_root(db)
        runs = db.scalars(
            select(BackupRun).order_by(BackupRun.created_at.desc(), BackupRun.id.desc()).limit(50)
        ).all()
        return templates.TemplateResponse(
            request=request,
            name="backup.html",
            context={
                "backup_root": str(root) if root else "",
                "runs": runs,
                "csrf_token": get_csrf_token(request),
                "error": request.query_params.get("error"),
                "success": request.query_params.get("success"),
            },
        )


@app.post("/settings/backup/root", dependencies=[Depends(csrf_protect)])
def update_backup_root_route(
    backup_root: str = Form(...),
    admin: AdminAccount = Depends(get_current_admin),
):
    try:
        with SessionLocal.begin() as db:
            save_backup_root(db, backup_root, admin.username)
    except (ValueError, OSError):
        return RedirectResponse("/settings/backup?error=path", status_code=303)
    return RedirectResponse("/settings/backup?success=path", status_code=303)


@app.post("/settings/backup/create", dependencies=[Depends(csrf_protect)])
def create_backup_route(
    backup_type: str = Form("manual"),
    admin: AdminAccount = Depends(get_current_admin),
):
    if backup_type not in {"daily", "weekly", "quarterly", "manual"}:
        return RedirectResponse("/settings/backup?error=type", status_code=303)
    with SessionLocal.begin() as db:
        run = create_backup(db, backup_type, admin.username)
        run_id = run.id
        status = run.status
    if status != "success":
        return RedirectResponse(f"/settings/backup?error=backup&run={run_id}", status_code=303)
    return RedirectResponse(f"/settings/backup?success=backup&run={run_id}", status_code=303)


@app.post("/settings/backup/restore", dependencies=[Depends(csrf_protect)])
def restore_backup_route(
    backup_id: int = Form(...),
    confirm_text: str = Form(...),
    reason: str = Form(...),
    db: Session = Depends(get_db),
    admin: AdminAccount = Depends(get_current_admin),
):
    if confirm_text.strip() != "RESTORE" or not reason.strip():
        return RedirectResponse("/settings/backup?error=confirm", status_code=303)
    run = db.get(BackupRun, backup_id)
    if run is None or run.status != "success" or not run.archive_path or not run.sha256:
        return RedirectResponse("/settings/backup?error=archive", status_code=303)
    archive_path = run.archive_path
    expected_sha256 = run.sha256
    db.close()
    try:
        restore_backup(
            archive_path,
            expected_sha256,
            actor=admin.username,
            reason=reason,
        )
    except Exception:
        return RedirectResponse("/settings/backup?error=restore", status_code=303)
    return RedirectResponse("/login?restored=1", status_code=303)


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
