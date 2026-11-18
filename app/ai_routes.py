"""Local AI assistant page and ask endpoint (advisory only)."""
from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.ai_assistant import answer_question, local_model_status
from app.config import settings
from app.db import SessionLocal
from app.security import csrf_token_for, verify_csrf_token


def register_ai_routes(app, templates: Jinja2Templates, require_admin):
    @app.get("/ai", response_class=HTMLResponse)
    def ai_page(request: Request):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        status = local_model_status()
        return templates.TemplateResponse(
            request=request,
            name="ai.html",
            context={
                "app_name": settings.app_name,
                "csrf_token": csrf_token_for(request),
                "status": status,
                "question": "",
                "answer": None,
                "used_model": False,
            },
        )

    @app.post("/ai/ask", response_class=HTMLResponse)
    def ai_ask(request: Request, question: str = Form(""), csrf_token: str = Form(...)):
        admin = require_admin(request)
        if isinstance(admin, RedirectResponse):
            return admin
        if not verify_csrf_token(request, csrf_token):
            return RedirectResponse("/ai", status_code=303)
        with SessionLocal() as db:
            result = answer_question(db, question)
        return templates.TemplateResponse(
            request=request,
            name="ai.html",
            context={
                "app_name": settings.app_name,
                "csrf_token": csrf_token_for(request),
                "status": result["status"],
                "question": question,
                "answer": result["answer"],
                "used_model": result["used_model"],
            },
        )
