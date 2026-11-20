import json
from datetime import datetime, timedelta, timezone

from app.ai_gateway import _canonical_json, plan_hash


def test_ai_plan_hash_is_stable_for_key_order():
    first = {"action": "approve_payroll_run", "payroll_run_id": 3}
    second = {"payroll_run_id": 3, "action": "approve_payroll_run"}
    assert _canonical_json(first) == _canonical_json(second)
    assert plan_hash(first) == plan_hash(second)


def test_ai_plan_hash_changes_when_plan_changes():
    first = {"action": "approve_payroll_run", "payroll_run_id": 3}
    changed = {"action": "approve_payroll_run", "payroll_run_id": 4}
    assert plan_hash(first) != plan_hash(changed)


def test_ai_gateway_models_are_local_data_only():
    from app.models import AICommandApproval, AIConversationMessage
    assert AICommandApproval.__tablename__ == "ai_command_approvals"
    assert AIConversationMessage.__tablename__ == "ai_conversation_messages"


def test_ai_approval_expiry_is_time_bounded():
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=10)
    assert expires > now
    assert expires <= now + timedelta(minutes=10)

def test_ai_gateway_normalizes_naive_database_expiry_to_utc():
    from app.ai_gateway import _as_utc

    naive = datetime(2026, 11, 20, 12, 0, 0)
    aware = _as_utc(naive)

    assert aware.tzinfo == timezone.utc
    assert aware == datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
