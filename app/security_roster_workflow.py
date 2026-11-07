"""Database-backed security roster proposal and explicit confirmation workflow."""
from datetime import date, datetime, time, timedelta
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import AdminAccount, AuditLog, Department, Employee, SecurityShiftAssignment, ShiftPattern
from app.security_roster import build_security_day_proposal


def _key(value):
    return " ".join(str(value or "").casefold().split())


def _shift_interval(work_date: date, start: time, end: time):
    start_at = datetime.combine(work_date, start)
    end_at = datetime.combine(work_date, end)
    if end_at <= start_at:
        end_at += timedelta(days=1)
    return start_at, end_at


def _intervals_overlap(first, second):
    return max(first[0], second[0]) < min(first[1], second[1])


def _cross_day_overlaps(left_date, left_assignments, right_date, right_assignments):
    overlaps = []
    for left in left_assignments:
        left_interval = _shift_interval(left_date, left["start_time"], left["end_time"])
        for right in right_assignments:
            right_interval = _shift_interval(right_date, right["start_time"], right["end_time"])
            if _intervals_overlap(left_interval, right_interval):
                overlaps.append((left, right))
    return overlaps


def _existing_adjacent_assignments(db, department_id, work_date):
    adjacent_dates = (work_date - timedelta(days=1), work_date + timedelta(days=1))
    return db.execute(
        select(SecurityShiftAssignment, ShiftPattern)
        .join(ShiftPattern, ShiftPattern.id == SecurityShiftAssignment.shift_pattern_id)
        .where(
            ShiftPattern.department_id == department_id,
            SecurityShiftAssignment.work_date.in_(adjacent_dates),
        )
    ).all()


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
    if len(employees) < 2:
        raise ValueError("security_department_must_have_at_least_two_active_employees")
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
        raise ValueError("excel_columns_must_match_all_active_security_employees")

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
    employee_names = {employee.id: employee.full_name for employee in employees}
    import_issues = parsed_workbook.get("issues", [])
    # Issues without a reliable date cannot be assigned to a particular day;
    # fail closed for the entire workbook rather than silently approving around them.
    workbook_blockers = [
        issue for issue in import_issues
        if issue.get("reason") == "duplicate_employee_header"
        or issue.get("reason") == "invalid_or_missing_date"
        or not issue.get("date")
    ]
    daily = []
    for record in parsed_workbook["records"]:
        roster = {employee_id: value for employee_id, value in record["roster"].items()}
        if set(roster) != set(resolved):
            raise ValueError("daily_roster_employee_mapping_is_incomplete")
        proposal = build_security_day_proposal(roster, pattern_data)
        date_issues = [
            issue for issue in import_issues
            if issue.get("date") == record["work_date"].isoformat()
        ]
        blocking_issues = workbook_blockers + date_issues
        if blocking_issues:
            proposal = {"status": "needs_review", "assignments": [], "issues": ["excel_import_issues_block_this_day"]}
        for assignment in proposal["assignments"]:
            assignment["employee_name"] = employee_names[assignment["employee_id"]]
        daily.append({"work_date": record["work_date"], "roster": roster, "proposal": proposal, "import_issues": blocking_issues, "can_confirm": proposal["status"] == "ready_for_admin_review"})

    # Surface same-date approvals during the initial preview as well as after
    # the confirm route refreshes it. The backend remains the final guard.
    if daily:
        approved_dates = set(db.scalars(
            select(SecurityShiftAssignment.work_date)
            .join(ShiftPattern, ShiftPattern.id == SecurityShiftAssignment.shift_pattern_id)
            .where(
                ShiftPattern.department_id == department.id,
                SecurityShiftAssignment.work_date.in_(
                    [day["work_date"] for day in daily]
                ),
            )
        ).all())
        for day in daily:
            if day["work_date"] in approved_dates:
                day["already_approved"] = True
                day["can_confirm"] = False

    # A shift may end after midnight, so reviewing each date in isolation can
    # miss an overlap with the following roster date. Compare adjacent proposals
    # as well as assignments already approved on an adjacent date.
    ordered_days = sorted(daily, key=lambda item: item["work_date"])
    for index, day in enumerate(ordered_days):
        day_assignments = day["proposal"]["assignments"]
        if not day_assignments:
            continue

        for next_day in ordered_days[index + 1:]:
            if (next_day["work_date"] - day["work_date"]).days != 1:
                break
            overlaps = _cross_day_overlaps(
                day["work_date"], day_assignments,
                next_day["work_date"], next_day["proposal"]["assignments"],
            )
            for left, right in overlaps:
                issue = (
                    "overlapping_security_shifts_across_work_dates:"
                    f"{day['work_date'].isoformat()}:{left['employee_id']}:"
                    f"{next_day['work_date'].isoformat()}:{right['employee_id']}"
                )
                for item in (day, next_day):
                    item["proposal"]["status"] = "needs_review"
                    item["proposal"]["issues"].append(issue)
                    item["can_confirm"] = False

        existing = _existing_adjacent_assignments(
            db, department.id, day["work_date"]
        )
        for existing_assignment, existing_pattern in existing:
            existing_date = existing_assignment.work_date
            existing_interval = _shift_interval(
                existing_date, existing_pattern.start_time, existing_pattern.end_time
            )
            for candidate in day_assignments:
                candidate_interval = _shift_interval(
                    day["work_date"], candidate["start_time"], candidate["end_time"]
                )
                if _intervals_overlap(candidate_interval, existing_interval):
                    issue = (
                        "overlapping_security_shift_with_approved_adjacent_day:"
                        f"{candidate['employee_id']}:{day['work_date'].isoformat()}:"
                        f"{existing_assignment.employee_id}:{existing_date.isoformat()}"
                    )
                    day["proposal"]["status"] = "needs_review"
                    day["proposal"]["issues"].append(issue)
                    day["can_confirm"] = False

    return {"department_id": department.id, "days": daily, "import_issues": import_issues}


def confirm_security_roster_day(db, work_date: date, roster: dict, admin: AdminAccount, import_issues: list | None = None):
    """Recompute and persist only a valid reviewed day for an authenticated admin."""
    if not isinstance(admin, AdminAccount) or admin.id is None:
        raise ValueError("authenticated_active_admin_required")
    persisted_admin = db.get(AdminAccount, admin.id)
    if (
        persisted_admin is None
        or not persisted_admin.active
        or persisted_admin.username != admin.username
    ):
        raise ValueError("authenticated_active_admin_required")
    department, employees = resolve_security_employees(db)
    import_issues = import_issues or []
    if any(
        issue.get("reason") in {"duplicate_employee_header", "invalid_or_missing_date"}
        or not issue.get("date")
        or issue.get("date") == work_date.isoformat()
        for issue in import_issues
    ):
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

    # Re-check adjacent already-approved dates at the persistence boundary so a
    # stale preview or out-of-order approval cannot create an overnight overlap.
    existing_adjacent = _existing_adjacent_assignments(db, department.id, work_date)
    for existing_assignment, existing_pattern in existing_adjacent:
        existing_interval = _shift_interval(
            existing_assignment.work_date,
            existing_pattern.start_time,
            existing_pattern.end_time,
        )
        for candidate in proposal["assignments"]:
            candidate_interval = _shift_interval(
                work_date, candidate["start_time"], candidate["end_time"]
            )
            if _intervals_overlap(candidate_interval, existing_interval):
                raise ValueError(
                    "security_roster_shift_overlaps_adjacent_approved_day"
                )

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
    try:
        # Force all inserts now so a partial assignment set cannot be mistaken
        # for a successful approval. The caller owns the surrounding transaction.
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError(
            "security_roster_approval_conflict_or_persistence_failure"
        ) from exc
    return proposal
