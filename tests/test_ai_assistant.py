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


def test_ai_command_requires_explicit_execute_prefix():
    from app.ai_agent import is_command_request, wants_execution
    assert is_command_request("اعتمد مسير الرواتب رقم 1")
    assert not wants_execution("اعتمد مسير الرواتب رقم 1")
    assert wants_execution("نفذ: اعتمد مسير الرواتب رقم 1")


def test_ai_command_allowlist_does_not_include_arbitrary_operations():
    from app.ai_agent import ALLOWED_ACTIONS
    assert "approve_payroll_run" in ALLOWED_ACTIONS
    assert "run_sql" not in ALLOWED_ACTIONS
    assert "shell" not in ALLOWED_ACTIONS
