"""Backup configuration, run, and restore endpoints."""
from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.backup_restore import restore_backup
from app.backup_service import create_backup, get_backup_root, list_recent_backups, save_backup_root
from app.config import settings
from app.db import SessionLocal
from app.security import csrf_token_for, verify_csrf_token


def register_backup_routes(app, templates: Jinja2Templates, require_admin):
    def _page(request, *, error=None, message=None):
        with SessionLocal() as db:
            root = get_backup_root(db)
            runs = list_recent_backups(db, limit=15)
            success_runs = [r for r in runs if r.status == "success" and r.archive_path and r.sha256]
            return templates.TemplateResponse(
                request=request,
                name="backup.html",
                context={
                    "app_name": settings.app_name,
                    "csrf_token": csrf_token_for(request),
                    "backup_root": str(root) if root else "",
                    "runs": runs,
                    "success_runs": success_runs,
                    "error": error,
                    "message": message,
                },
            )

    @app.get("/backup", response_class=HTMLResponse)
    def backup_page(request: Request):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        return _page(request)

    @app.post("/backup/configure")
    def backup_configure(request: Request, backup_root: str = Form(...), csrf_token: str = Form(...)):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return _page(request, error="انتهت صلاحية النموذج.")
        try:
            with SessionLocal() as db:
                save_backup_root(db, backup_root.strip(), actor=admin.username)
                db.commit()
        except ValueError as exc:
            msg = {
                "backup_path_must_be_absolute": "المسار يجب أن يكون مطلقاً.",
                "backup_path_not_writable": "المسار غير قابل للكتابة.",
            }.get(str(exc), "تعذر حفظ مسار النسخ.")
            return _page(request, error=msg)
        return _page(request, message="تم حفظ مسار النسخ الاحتياطي.")

    @app.post("/backup/run")
    def backup_run(
        request: Request,
        backup_type: str = Form("manual"),
        csrf_token: str = Form(...),
    ):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return _page(request, error="انتهت صلاحية النموذج.")
        with SessionLocal() as db:
            run = create_backup(db, backup_type=backup_type, actor=admin.username)
            db.commit()
            if run.status == "success":
                return _page(request, message=f"اكتملت النسخة والتحقق: {run.archive_path}")
            return _page(request, error=run.error_message or "فشل النسخ.")

    @app.post("/backup/restore")
    def backup_restore(
        request: Request,
        archive_path: str = Form(...),
        archive_sha256: str = Form(...),
        reason: str = Form(...),
        confirm_text: str = Form(...),
        csrf_token: str = Form(...),
    ):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return _page(request, error="انتهت صلاحية النموذج.")
        if confirm_text.strip() != "استعادة":
            return _page(
                request,
                error="للتأكيد اكتب كلمة «استعادة» حرفياً في حقل التأكيد.",
            )
        try:
            result = restore_backup(
                archive_path.strip(),
                archive_sha256.strip(),
                actor=admin.username,
                reason=reason,
            )
        except ValueError as exc:
            msg = {
                "restore_reason_required": "سبب الاستعادة مطلوب.",
                "backup_root_not_configured": "لم يُضبط مسار النسخ الخارجي.",
                "archive_outside_backup_root": "الأرشيف خارج مسار النسخ المسموح.",
                "backup_file_missing": "ملف النسخة غير موجود.",
                "archive_hash_mismatch": "هاش الأرشيف لا يطابق السجل.",
                "backup_archive_corrupt": "الأرشيف تالف.",
                "database_hash_mismatch": "هاش قاعدة البيانات داخل الأرشيف غير متطابق.",
            }.get(str(exc), f"تعذر الاستعادة: {exc}")
            return _page(request, error=msg)
        except RuntimeError as exc:
            return _page(request, error=str(exc)[:2000])
        return _page(
            request,
            message=(
                f"تمت الاستعادة بنجاح. نسخة pre-restore محفوظة في: "
                f"{result.get('pre_restore_archive')}"
            ),
        )
