from app.ai_assistant import build_system_prompt, local_fallback_answer


def test_ai_system_prompt_is_advisory():
    prompt = build_system_prompt()
    assert "لا تنفذ أي تغيير" in prompt
    assert "مراجعة وتأكيد" in prompt


def test_ai_fallback_reads_dashboard_metrics():
    answer = local_fallback_answer(
        "كم عدد الموظفين النشطين؟",
        {"metrics": {"active_employees": 7}},
    )
    assert answer == "عدد الموظفين النشطين حاليًا: 7."


def test_ai_fallback_keeps_core_system_usable_when_model_is_unavailable():
    answer = local_fallback_answer(
        "ساعدني في شيء آخر",
        {"metrics": {}},
    )
    assert "غير متاح حاليًا" in answer
