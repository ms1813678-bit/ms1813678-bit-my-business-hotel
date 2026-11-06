"""Database-backed security roster proposal and explicit confirmation workflow."""
from datetime import date, time
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import AuditLog, Department, Employee, SecurityShiftAssignment, ShiftPattern
from app.security_roster import build_security_day_proposal


def _key(value):
    return " ".join(str(value or "").casefold().split())


def resolve_security_employees(db):
    department = db.scalar(select(Department).where(Department.name == "Security"))
    if department is None:
        raise ValueError("security_department_not_configured")
    employees = db.scalars(
        select(Employee).where(
            Employee.department_id == department.id,
            Employee.status == "active",
        ).order_by(Employee.id)
    ).all()
    if len(employees) != 3:
        raise ValueError("security_department_must_have_exactly_three_active_employees")
    return department, employees


def prepare_security_roster(db, parsed_workbook):
    """Resolve spreadsheet headers exactly to active employees and prepare dates."""
    department, employees = resolve_security_employees(db)
    by_name = {}
    for employee in employees:
        for candidate in (employee.full_name, employee.code, employee.fingerprint_id):
            if candidate:
                key = _key(candidate)
                if key in by_name and by_name[key].id != employee.id:
                    raise ValueError("ambiguous_security_employee_identity")
                by_name[key] = employee

    resolved = {}
    for source_name, employee in parsed_workbook["employee_headers"].items():
        key = _key(source_name)
        match = by_name.get(key)
        if match is None:
            raise ValueError(f"unknown_security_employee_column:{source_name}")
        if match.id in resolved:
            raise ValueError("duplicate_security_employee_columns")
        resolved[match.id] = employee.id
    if set(resolved) != {employee.id for employee in employees}:
        raise ValueError("excel_columns_must_match_all_three_active_security_employees")

    patterns = db.scalars(
        select(ShiftPattern).where(
            ShiftPattern.department_id == department.id,
            ShiftPattern.active.is_(True),
        )
    ).all()
    pattern_data = [
        {"id": item.id, "start_time": item.start_time, "end_time": item.end_time}
        for item in patterns
    ]
    daily = []
    for record in parsed_workbook["records"]:
        roster = {resolved[employee_id]: value
                  for employee_id, value in record["roster"].items()}
        proposal = build_security_day_proposal(roster, pattern_data)
        daily.append({
            "work_date": record["work_date"],
            "roster": roster,
            "proposal": proposal,
        })
    return {"department_id": department.id, "days": daily,
            "import_issues": parsed_workbook.get("issues", [])}


def confirm_security_roster_day(db, work_date: date, roster: dict, admin_username: str):
    """Recompute from raw values, then persist only a fully valid reviewed day."""
    department, employees = resolve_security_employees(db)
    employee_ids = {employee.id for employee in employees}
    if set(roster) != employee_ids:
        raise ValueError("roster_employee_set_changed_since_preview")

    patterns = db.scalars(
        select(ShiftPattern).where(
            ShiftPattern.department_id == department.id,
            ShiftPattern.active.is_(True),
        )
    ).all()
    pattern_data = [
        {"id": item.id, "start_time": item.start_time, "end_time": item.end_time}
        for item in patterns
    ]
    proposal = build_security_day_proposal(roster, pattern_data)
    if proposal["status"] != "ready_for_admin_review":
        raise ValueError("security_roster_day_requires_manual_correction")

    existing = db.scalars(
        select(SecurityShiftAssignment).where(
            SecurityShiftAssignment.work_date == work_date,
            SecurityShiftAssignment.employee_id.in_(employee_ids),
        )
    ).all()
    if existing:
        raise ValueError("security_roster_date_already_has_assignments_review_before_replacing")

    for item in proposal["assignments"]:
        db.add(SecurityShiftAssignment(
            employee_id=item["employee_id"],
            work_date=work_date,
            shift_pattern_id=item["shift_pattern_id"],
            notes=f"Approved from security roster by {admin_username}; {item['duration_hours']}h",
        ))
    db.add(AuditLog(
        actor=admin_username,
        action="security_roster_day_approved",
        entity_type="security_roster",
        entity_id=work_date.isoformat(),
        new_value="; ".join(
            f"{employee_id}:{value if isinstance(value, str) else value.strftime('%H:%M')}"
            for employee_id, value in sorted(roster.items())
        ),
        reason="Administrator confirmed reviewed security roster day",
    ))
    return proposal
