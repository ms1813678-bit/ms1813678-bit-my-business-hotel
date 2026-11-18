"""Required local-only AI assistant (Ollama-compatible, loopback only).

Core HR/payroll paths never depend on AI availability. When the local model is
unavailable the UI shows a clear status and deterministic fallback answers.
The assistant is advisory only: it never saves, approves, or mutates data.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AttendanceDailyRecord, Employee, PayrollPeriod

OLLAMA_BASE_URL = os.getenv("LOCAL_AI_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("LOCAL_AI_MODEL", "qwen2.5:3b")
TIMEOUT_SECONDS = 1.5


def _ensure_loopback() -> None:
    host = urlparse(OLLAMA_BASE_URL).hostname
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("الاتصال بالنموذج مسموح فقط على الجهاز المحلي (127.0.0.1).")


def _post_json(path: str, payload: dict[str, Any], timeout: float = TIMEOUT_SECONDS) -> dict[str, Any]:
    _ensure_loopback()
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        f"{OLLAMA_BASE_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def local_model_status() -> dict[str, Any]:
    """Return availability without contacting any remote service."""
    try:
        _ensure_loopback()
    except RuntimeError as exc:
        return {
            "available": False,
            "server": False,
            "model": OLLAMA_MODEL,
            "message": str(exc),
        }
    req = request.Request(f"{OLLAMA_BASE_URL}/api/tags", method="GET")
    try:
        with request.urlopen(req, timeout=0.8) as response:
            data = json.loads(response.read().decode("utf-8"))
        names = {str(item.get("name", "")) for item in data.get("models", [])}
        installed = OLLAMA_MODEL in names or any(
            name.startswith(f"{OLLAMA_MODEL}:") for name in names
        )
        return {
            "available": installed,
            "server": True,
            "model": OLLAMA_MODEL,
            "message": (
                "المساعد المحلي جاهز."
                if installed
                else "خادم Ollama يعمل، لكن النموذج المطلوب غير مثبت."
            ),
        }
    except (OSError, ValueError, error.URLError):
        return {
            "available": False,
            "server": False,
            "model": OLLAMA_MODEL,
            "message": (
                "المساعد المحلي غير متاح حاليًا. النظام الأساسي يعمل بشكل طبيعي."
            ),
        }


def build_system_prompt() -> str:
    return (
        "أنت مساعد موارد بشرية محلي لنظام M.Y. Business Hotel. "
        "أجب بالعربية الواضحة وباختصار. استخدم البيانات التي يرسلها النظام فقط. "
        "لا تخترع بيانات أو أرقامًا. لا تعتمد راتبًا أو إجازة أو حضورًا أو سلفة، "
        "ولا تنفذ أي تغيير؛ أنت مساعد تحليلي فقط. إذا كانت البيانات ناقصة فاذكر ذلك. "
        "عند اقتراح إجراء مؤثر، اعرضه كاقتراح يحتاج مراجعة وتأكيد مسؤول الموارد البشرية."
    )


def build_read_only_context(db: Session) -> dict[str, Any]:
    """Safe aggregate snapshot for the assistant; never includes secrets."""
    active_employees = db.scalar(
        select(func.count()).select_from(Employee).where(Employee.status == "active")
    ) or 0
    attendance_review = db.scalar(
        select(func.count())
        .select_from(AttendanceDailyRecord)
        .where(AttendanceDailyRecord.status.in_(("needs_review", "ready_for_review")))
    ) or 0
    payroll_open = db.scalar(
        select(func.count())
        .select_from(PayrollPeriod)
        .where(PayrollPeriod.status.in_(("draft", "reopened")))
    ) or 0
    return {
        "metrics": {
            "active_employees": int(active_employees),
            "attendance_review": int(attendance_review),
            "payroll_drafts": int(payroll_open),
        }
    }


def ask_local_model(question: str, context: dict[str, Any]) -> str:
    """Ask the configured local model; raises RuntimeError when unavailable."""
    status = local_model_status()
    if not status["available"]:
        raise RuntimeError(status["message"])
    response = _post_json(
        "/api/chat",
        {
            "model": OLLAMA_MODEL,
            "stream": False,
            "messages": [
                {"role": "system", "content": build_system_prompt()},
                {
                    "role": "user",
                    "content": (
                        f"سؤال المسؤول: {question.strip()}\n\n"
                        f"بيانات النظام الحالية (للقراءة فقط): "
                        f"{json.dumps(context, ensure_ascii=False)}"
                    ),
                },
            ],
        },
        timeout=30,
    )
    answer = str(response.get("message", {}).get("content", "")).strip()
    if not answer:
        raise RuntimeError("لم يُرجع النموذج المحلي إجابة.")
    return answer


def local_fallback_answer(question: str, context: dict[str, Any]) -> str:
    """Deterministic help when no model is installed or reachable."""
    q = " ".join(question.casefold().split())
    metrics = context.get("metrics", {})
    if any(term in q for term in ("عدد الموظفين", "الموظفين النشطين", "الموظفون النشطون")):
        return f"عدد الموظفين النشطين حاليًا: {metrics.get('active_employees', 0)}."
    if "الحضور" in q and ("مراجعة" in q or "مشكلة" in q):
        return (
            f"عدد سجلات الحضور التي تحتاج مراجعة حاليًا: "
            f"{metrics.get('attendance_review', 0)}."
        )
    if "الرواتب" in q and ("مفتوح" in q or "مسير" in q):
        return (
            f"عدد مسيرات الرواتب المفتوحة أو المعاد فتحها: "
            f"{metrics.get('payroll_drafts', 0)}."
        )
    return (
        "المساعد الذكي المحلي غير متاح حاليًا لأن النموذج المحلي غير مثبت أو غير مشغّل. "
        "يمكنك استخدام النظام كاملًا بشكل طبيعي. بعد تشغيل Ollama وتثبيت النموذج "
        f"({OLLAMA_MODEL}) سأتمكن من تحليل السؤال باستخدام بيانات النظام فقط، "
        "دون حفظ أي تغيير تلقائي."
    )


def answer_question(db: Session, question: str) -> dict[str, Any]:
    """Public entry: always returns an answer string and availability flags."""
    context = build_read_only_context(db)
    status = local_model_status()
    if not question.strip():
        return {
            "answer": "اكتب سؤالاً عن الموظفين أو الحضور أو الرواتب.",
            "used_model": False,
            "status": status,
        }
    if status["available"]:
        try:
            return {
                "answer": ask_local_model(question, context),
                "used_model": True,
                "status": status,
            }
        except RuntimeError as exc:
            return {
                "answer": local_fallback_answer(question, context) + f" ({exc})",
                "used_model": False,
                "status": status,
            }
    return {
        "answer": local_fallback_answer(question, context),
        "used_model": False,
        "status": status,
    }
