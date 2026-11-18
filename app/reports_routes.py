"""Reports page and Excel export endpoints."""
from datetime import date, timedelta

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.reports_service import build_hr_workbook
from app.security import csrf_token_for, verify_csrf_token


def register_reports_routes(app, templates: Jinja2Templates, require_admin):
    """Attach /reports and /reports/export to the FastAPI application."""

    @app.get("/reports", response_class=HTMLResponse)
    def reports_page(request: Request):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        today = date.today()
        first_of_month = today.replace(day=1)
        return templates.TemplateResponse(
            request=request,
            name="reports.html",
            context={
                "app_name": settings.app_name,
                "csrf_token": csrf_token_for(request),
                "start_date": first_of_month.isoformat(),
                "end_date": today.isoformat(),
                "error": None,
            },
        )

    @app.post("/reports/export")
    def reports_export(
        request: Request,
        start_date: date = Form(...),
        end_date: date = Form(...),
        csrf_token: str = Form(...),
    ):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return templates.TemplateResponse(
                request=request,
                name="reports.html",
                context={
                    "app_name": settings.app_name,
                    "csrf_token": csrf_token_for(request),
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "error": "انتهت صلاحية النموذج. حدّث الصفحة وحاول مرة أخرى.",
                },
                status_code=403,
            )
        if end_date < start_date:
            return templates.TemplateResponse(
                request=request,
                name="reports.html",
                context={
                    "app_name": settings.app_name,
                    "csrf_token": csrf_token_for(request),
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "error": "تاريخ النهاية يجب أن يكون بعد أو يساوي تاريخ البداية.",
                },
                status_code=400,
            )
        try:
            with SessionLocal() as db:
                data = build_hr_workbook(db, start_date, end_date)
        except ValueError as exc:
            return templates.TemplateResponse(
                request=request,
                name="reports.html",
                context={
                    "app_name": settings.app_name,
                    "csrf_token": csrf_token_for(request),
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "error": str(exc),
                },
                status_code=400,
            )

        filename = f"hr-report-{start_date.isoformat()}-to-{end_date.isoformat()}.xlsx"
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
