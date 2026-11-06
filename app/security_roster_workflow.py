"""Database-backed security roster proposal and explicit confirmation workflow."""
from datetime import date, time
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import AdminAccount, AuditLog, Department, Employee, SecurityShiftAssignment, ShiftPattern
from app.security_roster import build_security_day_proposal


def _key(value):
    return " ".join(str(value or "").casefold().split())


def resolve_security_employees(db):
    department = db.scalar(select(Department).where(Department.name == "Security", Department.active.is_(True)).with_for_update())
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
    for employee_id, source_name in parsed_workbook["employee_headers"].items():
        employee = next((item for item in employees if item.id == employee_id), None)
        if employee is None:
            raise ValueError("excel_employee_id_not_in_active_security_team")
        allowed = {_key(value) for value in (employee.full_name, employee.code, employee.fingerprint_id) if value}
        if _key(source_name) not in allowed:
            raise ValueError(f"unknown_or_mismatched_security_employee_column:{source_name}")
        if employee_id in resolved:
            raise ValueError("duplicate_security_employee_columns")
        resolved[employee_id] = employee_id
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
    import_issues = parsed_workbook.get("issues", [])
    daily = []
    for record in parsed_workbook["records"]:
        roster = {employee_id: value for employee_id, value in record["roster"].items()}
        if set(roster) != set(resolved):
            raise ValueError("daily_roster_employee_mapping_is_incomplete")
        proposal = build_security_day_proposal(roster, pattern_data)
        date_issues = [issue for issue in import_issues if issue.get("date") == record["work_date"].isoformat() or issue.get("reason") == "duplicate_employee_header"]
        if date_issues:
            proposal = {"status": "needs_review", "assignments": [], "issues": ["excel_import_issues_block_this_day"]}
        daily.append({"work_date": record["work_date"], "roster": roster, "proposal": proposal, "import_issues": date_issues, "can_confirm": proposal["status"] == "ready_for_admin_review"})
    return {"department_id": department.id, "days": daily, "import_issues": import_issues}


def confirm_security_roster_day(db, work_date: date, roster: dict, admin: AdminAccount, import_issues: list | None = None):
    """Recompute and persist only a valid reviewed day for an authenticated admin."""
    if not isinstance(admin, AdminAccount) or not admin.active or not admin.username.strip():
        raise ValueError("authenticated_active_admin_required")
    department, employees = resolve_security_employees(db)
    import_issues = import_issues or []
    if any(issue.get("reason") == "duplicate_employee_header" or issue.get("date") == work_date.isoformat() for issue in import_issues):
        raise ValueError("security_roster_import_issues_block_day_approval")
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
            notes=f"Approved from security roster by {admin.username}; {item['duration_hours']}h",
        ))
    db.add(AuditLog(
        actor=admin.username,
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
