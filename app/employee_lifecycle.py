"""Employee profile history, archival, and rehire operations."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Branch, Department, Employee, EmployeeChange, EmployeeServicePeriod


EDITABLE_FIELDS = (
    "full_name", "national_id", "phone", "address", "department_id", "branch_id",
    "position", "hire_date", "basic_salary", "daily_hours", "fingerprint_id",
    "probation_start_date", "probation_end_date",
)


def _value_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _employee_for_update(db: Session, employee_id: int) -> Employee:
    employee = db.scalar(select(Employee).where(Employee.id == employee_id).with_for_update())
    if employee is None:
        raise ValueError("employee_not_found")
    return employee


def update_employee_profile(
    db: Session, *, employee_id: int, changes: dict, effective_date: date,
    reason: str, actor: str,
) -> Employee:
    """Apply profile changes and retain field-level history and audit evidence."""
    reason = reason.strip()
    if not reason:
        raise ValueError("employee_change_reason_required")
    if set(changes) - set(EDITABLE_FIELDS):
        raise ValueError("employee_change_field_not_editable")

    employee = _employee_for_update(db, employee_id)
    if employee.status != "active":
        raise ValueError("terminated_employee_cannot_be_edited")

    new_hire_date = changes.get("hire_date", employee.hire_date)
    if effective_date < new_hire_date:
        raise ValueError("employee_change_date_before_hire_date")
    if "hire_date" in changes and new_hire_date != employee.hire_date:
        previous_period = db.scalar(
            select(EmployeeServicePeriod)
            .where(
                EmployeeServicePeriod.employee_id == employee.id,
                EmployeeServicePeriod.end_date.is_not(None),
            )
            .order_by(EmployeeServicePeriod.end_date.desc())
            .limit(1)
        )
        if previous_period is not None and new_hire_date < previous_period.end_date:
            raise ValueError("employee_hire_date_before_previous_service_end")

    department_id = changes.get("department_id", employee.department_id)
    department = db.get(Department, department_id)
    if department is None or not department.active:
        raise ValueError("employee_department_unavailable")
    branch_id = changes.get("branch_id", employee.branch_id)
    if branch_id is not None:
        branch = db.get(Branch, branch_id)
        if branch is None or not branch.active or branch.department_id != department_id:
            raise ValueError("employee_branch_not_in_department")

    name = changes.get("full_name", employee.full_name)
    position = changes.get("position", employee.position)
    salary = changes.get("basic_salary", employee.basic_salary)
    hours = changes.get("daily_hours", employee.daily_hours)
    if not name or not name.strip() or not position or not position.strip():
        raise ValueError("employee_name_and_position_required")
    if salary < 0 or hours <= 0 or hours > 24:
        raise ValueError("employee_salary_or_hours_invalid")

    old_values, new_values = {}, {}
    for field in EDITABLE_FIELDS:
        if field not in changes:
            continue
        old_value = getattr(employee, field)
        new_value = changes[field]
        if new_value == old_value:
            continue
        setattr(employee, field, new_value)
        old_values[field] = _value_text(old_value)
        new_values[field] = _value_text(new_value)
        db.add(EmployeeChange(
            employee_id=employee.id, field_name=field,
            old_value=old_values[field], new_value=new_values[field],
            effective_date=effective_date, reason=reason,
        ))

    if "hire_date" in new_values:
        open_period = db.scalar(
            select(EmployeeServicePeriod)
            .where(
                EmployeeServicePeriod.employee_id == employee.id,
                EmployeeServicePeriod.end_date.is_(None),
            )
            .with_for_update()
        )
        if open_period is None:
            raise ValueError("employee_open_service_period_missing")
        open_period.start_date = new_hire_date
        open_period.start_reason = f"Employment start corrected: {reason}"

    if new_values:
        db.add(AuditLog(
            actor=actor, action="employee_profile_updated", entity_type="employee",
            entity_id=str(employee.id),
            old_value=json.dumps(old_values, ensure_ascii=False, sort_keys=True),
            new_value=json.dumps(new_values, ensure_ascii=False, sort_keys=True),
            reason=reason,
        ))
        db.flush()
    return employee


def archive_employee(
    db: Session, *, employee_id: int, effective_date: date, reason: str, actor: str,
) -> Employee:
    reason = reason.strip()
    if not reason:
        raise ValueError("employee_archive_reason_required")
    employee = _employee_for_update(db, employee_id)
    if employee.status != "active":
        raise ValueError("employee_not_active")
    period = db.scalar(
        select(EmployeeServicePeriod)
        .where(
            EmployeeServicePeriod.employee_id == employee.id,
            EmployeeServicePeriod.end_date.is_(None),
        )
        .with_for_update()
    )
    if period is None:
        raise ValueError("employee_open_service_period_missing")
    if effective_date < period.start_date:
        raise ValueError("employee_archive_date_before_service_start")

    period.end_date = effective_date
    period.end_reason = reason
    employee.status = "terminated"
    db.add(EmployeeChange(
        employee_id=employee.id, field_name="status", old_value="active",
        new_value="terminated", effective_date=effective_date, reason=reason,
    ))
    db.add(AuditLog(
        actor=actor, action="employee_archived", entity_type="employee",
        entity_id=str(employee.id),
        old_value=json.dumps({"status": "active", "service_period_end": None}),
        new_value=json.dumps({
            "status": "terminated", "service_period_end": effective_date.isoformat()
        }),
        reason=reason,
    ))
    db.flush()
    return employee


def rehire_employee(
    db: Session, *, employee_id: int, start_date: date, reason: str, actor: str,
) -> Employee:
    reason = reason.strip()
    if not reason:
        raise ValueError("employee_rehire_reason_required")
    employee = _employee_for_update(db, employee_id)
    if employee.status == "active":
        raise ValueError("employee_already_active")
    last_period = db.scalar(
        select(EmployeeServicePeriod)
        .where(EmployeeServicePeriod.employee_id == employee.id)
        .order_by(EmployeeServicePeriod.start_date.desc(), EmployeeServicePeriod.id.desc())
        .limit(1).with_for_update()
    )
    if last_period is not None and last_period.end_date is None:
        raise ValueError("employee_open_service_period_already_exists")
    if last_period is not None and start_date < last_period.end_date:
        raise ValueError("employee_rehire_date_before_last_service_end")

    old_hire_date = employee.hire_date
    employee.status = "active"
    employee.hire_date = start_date
    db.add(EmployeeServicePeriod(
        employee_id=employee.id, start_date=start_date, start_reason=reason,
    ))
    for field, old_value, new_value in (
        ("status", "terminated", "active"), ("hire_date", old_hire_date, start_date),
    ):
        db.add(EmployeeChange(
            employee_id=employee.id, field_name=field,
            old_value=_value_text(old_value), new_value=_value_text(new_value),
            effective_date=start_date, reason=reason,
        ))
    db.add(AuditLog(
        actor=actor, action="employee_rehired", entity_type="employee",
        entity_id=str(employee.id),
        old_value=json.dumps({"status": "terminated", "hire_date": _value_text(old_hire_date)}),
        new_value=json.dumps({"status": "active", "hire_date": start_date.isoformat()}),
        reason=reason,
    ))
    db.flush()
    return employee
