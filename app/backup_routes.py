"""Backup configuration and run endpoints."""
from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.backup_service import create_backup, get_backup_root, list_recent_backups, save_backup_root
from app.config import settings
from app.db import SessionLocal
from app.models_backup import BackupRun
from app.security import csrf_token_for, verify_csrf_token


def register_backup_routes(app, templates: Jinja2Templates, require_admin):
    @app.get("/backup", response_class=HTMLResponse)
    def backup_page(request: Request):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        with SessionLocal() as db:
            root = get_backup_root(db)
            runs = list_recent_backups(db, limit=15)
            return templates.TemplateResponse(
                request=request,
                name="backup.html",
                context={
                    "app_name": settings.app_name,
                    "csrf_token": csrf_token_for(request),
                    "backup_root": str(root) if root else "",
                    "runs": runs,
                    "error": None,
                    "message": None,
                },
            )

    @app.post("/backup/configure")
    def backup_configure(request: Request, backup_root: str = Form(...), csrf_token: str = Form(...)):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return RedirectResponse("/backup", status_code=303)
        try:
            with SessionLocal() as db:
                save_backup_root(db, backup_root.strip(), actor=admin.username)
                db.commit()
        except ValueError:
            return RedirectResponse("/backup", status_code=303)
        return RedirectResponse("/backup", status_code=303)

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
            return RedirectResponse("/backup", status_code=303)
        with SessionLocal() as db:
            create_backup(db, backup_type=backup_type, actor=admin.username)
            db.commit()
        return RedirectResponse("/backup", status_code=303)
