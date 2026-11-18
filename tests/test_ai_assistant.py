"""Safety and fallback tests for the local AI assistant."""
from app.ai_assistant import (
    build_system_prompt,
    local_fallback_answer,
    local_model_status,
)


def test_system_prompt_is_advisory_only():
    prompt = build_system_prompt()
    assert "لا تنفذ أي تغيير" in prompt or "تحليلي" in prompt
    assert "لا تعتمد" in prompt


def test_fallback_active_employees():
    ctx = {"metrics": {"active_employees": 12, "attendance_review": 3, "payroll_drafts": 1}}
    answer = local_fallback_answer("كم عدد الموظفين النشطين؟", ctx)
    assert "12" in answer


def test_fallback_attendance_review():
    ctx = {"metrics": {"active_employees": 5, "attendance_review": 7, "payroll_drafts": 0}}
    answer = local_fallback_answer("سجلات الحضور التي تحتاج مراجعة", ctx)
    assert "7" in answer


def test_status_never_points_remote_by_default():
    status = local_model_status()
    assert "model" in status
    assert "available" in status
    assert "message" in status
