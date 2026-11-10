"""Optional local-only AI assistant.

The assistant talks only to a model listening on localhost (Ollama-compatible API).
If no local model is available, the application remains fully usable and the UI
reports that the assistant is unavailable.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlparse
from typing import Any
from urllib import error, request

OLLAMA_BASE_URL = os.getenv("LOCAL_AI_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("LOCAL_AI_MODEL", "qwen2.5:3b")

def _ensure_loopback() -> None:
    host = urlparse(OLLAMA_BASE_URL).hostname
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("الاتصال بالنموذج مسموح فقط على الجهاز المحلي.")

TIMEOUT_SECONDS = 1.5


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
    try:
        _ensure_loopback()
    except RuntimeError as exc:
        return {"available": False, "server": False, "model": OLLAMA_MODEL, "message": str(exc)}
    """Return availability without ever contacting a remote service."""
    req = request.Request(f"{OLLAMA_BASE_URL}/api/tags", method="GET")
    try:
        with request.urlopen(req, timeout=0.8) as response:
            data = json.loads(response.read().decode("utf-8"))
        names = {str(item.get("name", "")) for item in data.get("models", [])}
        installed = OLLAMA_MODEL in names or any(name.startswith(f"{OLLAMA_MODEL}:") for name in names)
        return {
            "available": installed,
            "server": True,
            "model": OLLAMA_MODEL,
            "message": "المساعد المحلي جاهز." if installed else "خادم الذكاء المحلي يعمل، لكن النموذج المطلوب غير مثبت.",
        }
    except (OSError, ValueError, error.URLError):
        return {
            "available": False,
            "server": False,
            "model": OLLAMA_MODEL,
            "message": "المساعد المحلي غير متاح حاليًا. النظام الأساسي يعمل بشكل طبيعي.",
        }


def build_system_prompt() -> str:
    return (
        "أنت مساعد موارد بشرية محلي لنظام M.Y. Business Hotel. "
        "أجب بالعربية الواضحة وباختصار. استخدم البيانات التي يرسلها النظام فقط. "
        "لا تخترع بيانات أو أرقامًا. لا تعتمد راتبًا أو إجازة أو حضورًا أو سلفة، "
        "ولا تنفذ أي تغيير؛ أنت مساعد تحليلي فقط. إذا كانت البيانات ناقصة فاذكر ذلك. "
        "عند اقتراح إجراء مؤثر، اعرضه كاقتراح يحتاج مراجعة وتأكيد مسؤول الموارد البشرية."
    )


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
                        f"بيانات النظام الحالية (للقراءة فقط): {json.dumps(context, ensure_ascii=False)}"
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
    """Provide useful deterministic help even when no model is installed."""
    q = " ".join(question.casefold().split())
    metrics = context.get("metrics", {})
    if any(term in q for term in ("عدد الموظفين", "الموظفين النشطين", "الموظفون النشطون")):
        return f"عدد الموظفين النشطين حاليًا: {metrics.get('active_employees', 0)}."
    if "الحضور" in q and ("مراجعة" in q or "مشكلة" in q):
        return f"عدد سجلات الحضور التي تحتاج مراجعة حاليًا: {metrics.get('attendance_review', 0)}."
    if "الرواتب" in q and ("مفتوح" in q or "مسير" in q):
        return f"عدد مسيرات الرواتب المفتوحة أو المعاد فتحها: {metrics.get('payroll_drafts', 0)}."
    return (
        "المساعد الذكي المحلي غير متاح حاليًا لأن النموذج المحلي غير مثبت أو غير مشغّل. "
        "يمكنك استخدام النظام كاملًا بشكل طبيعي. بعد تشغيل النموذج المحلي سأتمكن من تحليل السؤال "
        "باستخدام بيانات النظام المعروضة للمساعد، دون حفظ أي تغيير تلقائي."
    )


def ask_local_model_json(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    status = local_model_status()
    if not status["available"]:
        raise RuntimeError(status["message"])
    response = _post_json(
        "/api/chat",
        {"model": OLLAMA_MODEL, "stream": False, "format": "json",
         "messages": [{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_prompt}]},
        timeout=30,
    )
    raw = str(response.get("message", {}).get("content", "")).strip()
    if not raw:
        raise RuntimeError("لم يُرجع النموذج المحلي خطة أمر.")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("النموذج المحلي لم يُرجع JSON صالحًا.") from exc
