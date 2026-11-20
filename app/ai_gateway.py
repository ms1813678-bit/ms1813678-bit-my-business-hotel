"""Approval gateway for the local HR AI command agent.

The model may prepare a plan, but every state-changing command must pass through
this gateway. Approval tokens are single-use, hashed at rest, time-limited, and
bound to the administrator who requested the plan.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.ai_agent import build_plan, execute_plan
from app.db import SessionLocal
from app.models import AICommandApproval, AIConversationMessage, AdminAccount, AuditLog

APPROVAL_TTL_MINUTES = 10


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def plan_hash(plan: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(plan).encode("utf-8")).hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    """Normalize database timestamps from SQLite/PostgreSQL to aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _memory(actor: str, role: str, content: str) -> None:
    with SessionLocal.begin() as db:
        db.add(AIConversationMessage(actor=actor, role=role, content=content[:10000]))


def recent_memory(actor: str, limit: int = 12) -> list[dict[str, str]]:
    with SessionLocal() as db:
        rows = db.scalars(
            select(AIConversationMessage)
            .where(AIConversationMessage.actor == actor)
            .order_by(AIConversationMessage.id.desc())
            .limit(limit)
        ).all()
    rows.reverse()
    return [{"role": row.role, "content": row.content} for row in rows]


def prepare_command(question: str, actor: str) -> dict[str, Any]:
    history = recent_memory(actor, limit=8)
    history_text = "\n".join(f"{item['role']}: {item['content']}" for item in history)
    planning_question = question
    if history_text:
        planning_question = f"السياق السابق للمحادثة (للفهم فقط):\\n{history_text}\\n\\nالأمر الحالي: {question}"
    plan = build_plan(planning_question)
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=APPROVAL_TTL_MINUTES)
    summary = str(plan.get("summary") or "تم إعداد خطة للتنفيذ بعد الموافقة.")
    with SessionLocal.begin() as db:
        db.add(AICommandApproval(
            token_hash=_token_hash(token),
            actor=actor,
            question=question,
            plan_json=_canonical_json(plan),
            plan_hash=plan_hash(plan),
            status="pending",
            created_at=now,
            expires_at=expires,
        ))
        db.add(AuditLog(
            actor=actor,
            action="ai_command_proposed",
            entity_type="ai_command",
            entity_id=_token_hash(token),
            new_value=f"action={plan.get('action')}; plan_hash={plan_hash(plan)}",
            reason=question,
        ))
    _memory(actor, "user", question)
    _memory(actor, "assistant", f"خطة معلقة تحتاج موافقة: {summary}")
    return {
        "token": token,
        "plan": plan,
        "summary": summary,
        "expires_at": expires,
    }


def reject_command(token: str, actor: str, reason: str = "رفض المسؤول العملية.") -> str:
    now = datetime.now(timezone.utc)
    expired = False
    with SessionLocal.begin() as db:
        approval = db.scalar(
            select(AICommandApproval)
            .where(AICommandApproval.token_hash == _token_hash(token))
            .with_for_update()
        )
        if approval is None or approval.actor != actor:
            raise ValueError("طلب الموافقة غير موجود.")
        if approval.status != "pending":
            raise ValueError("هذا الطلب لم يعد قابلًا للرفض.")
        if _as_utc(approval.expires_at) <= now:
            approval.status = "expired"
            expired = True
            db.add(AuditLog(
                actor=actor, action="ai_command_expired", entity_type="ai_command",
                entity_id=str(approval.id), reason="انتهت صلاحية طلب الموافقة.",
            ))
        else:
            approval.status = "rejected"
            approval.rejected_at = now
            db.add(AuditLog(
                actor=actor, action="ai_command_rejected", entity_type="ai_command",
                entity_id=str(approval.id), reason=reason,
            ))
    # Raise only after the transaction commits, or the terminal state would roll back.
    if expired:
        raise ValueError("انتهت صلاحية طلب الموافقة.")
    _memory(actor, "assistant", "تم رفض العملية ولم يتم تغيير بيانات النظام.")
    return "تم رفض العملية. لم يتم تغيير أي بيانات."


def approve_command(token: str, actor: str) -> str:
    now = datetime.now(timezone.utc)
    plan: dict[str, Any] | None = None
    terminal_error: str | None = None
    with SessionLocal.begin() as db:
        approval = db.scalar(
            select(AICommandApproval)
            .where(AICommandApproval.token_hash == _token_hash(token))
            .with_for_update()
        )
        if approval is None or approval.actor != actor:
            raise ValueError("طلب الموافقة غير موجود.")
        if approval.status != "pending":
            raise ValueError("هذا الطلب سبق التعامل معه ولا يمكن تنفيذه مرة أخرى.")
        if _as_utc(approval.expires_at) <= now:
            approval.status = "expired"
            db.add(AuditLog(
                actor=actor, action="ai_command_expired", entity_type="ai_command",
                entity_id=str(approval.id), reason="انتهت صلاحية طلب الموافقة.",
            ))
            terminal_error = "انتهت صلاحية طلب الموافقة."
        else:
            try:
                candidate = json.loads(approval.plan_json)
                if not isinstance(candidate, dict):
                    raise ValueError("stored plan is not a JSON object")
                candidate_hash = plan_hash(candidate)
            except (TypeError, ValueError, json.JSONDecodeError):
                candidate = None
                candidate_hash = None
            if candidate is None or candidate_hash != approval.plan_hash:
                approval.status = "invalidated"
                db.add(AuditLog(
                    actor=actor, action="ai_command_invalidated", entity_type="ai_command",
                    entity_id=str(approval.id),
                    reason="خطة التنفيذ المخزنة غير صالحة أو لم تعد مطابقة للخطة المعروضة.",
                ))
                terminal_error = "خطة التنفيذ لم تعد مطابقة للخطة التي تم عرضها."
            else:
                plan = candidate
                approval.status = "executing"
                approval.approved_at = now
                db.add(AuditLog(
                    actor=actor, action="ai_command_approved", entity_type="ai_command",
                    entity_id=str(approval.id), new_value=f"plan_hash={approval.plan_hash}",
                    reason="موافقة صريحة من مسؤول الموارد البشرية.",
                ))
    # Persist expired/invalidated states before surfacing the corresponding error.
    if terminal_error is not None:
        raise ValueError(terminal_error)
    if plan is None:
        raise RuntimeError("لم يتم تجهيز خطة تنفيذ صالحة بعد الموافقة.")
    try:
        message = execute_plan(plan, actor)
    except Exception as exc:
        with SessionLocal.begin() as db:
            approval = db.scalar(
                select(AICommandApproval)
                .where(AICommandApproval.token_hash == _token_hash(token))
                .with_for_update()
            )
            if approval is not None and approval.status == "executing":
                approval.status = "failed"
                approval.failure_reason = str(exc)[:2000]
                db.add(AuditLog(
                    actor=actor, action="ai_command_failed", entity_type="ai_command",
                    entity_id=str(approval.id), reason=str(exc)[:2000],
                ))
        raise
    with SessionLocal.begin() as db:
        approval = db.scalar(
            select(AICommandApproval)
            .where(AICommandApproval.token_hash == _token_hash(token))
            .with_for_update()
        )
        if approval is None or approval.status != "executing":
            raise RuntimeError("حالة الموافقة غير صالحة بعد التنفيذ.")
        approval.status = "consumed"
        approval.consumed_at = datetime.now(timezone.utc)
        db.add(AuditLog(
            actor=actor, action="ai_command_executed", entity_type="ai_command",
            entity_id=str(approval.id), new_value=f"plan_hash={approval.plan_hash}",
            reason=message,
        ))
    _memory(actor, "assistant", message)
    return message
