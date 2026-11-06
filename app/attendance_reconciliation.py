"""Conservative attendance reconciliation proposals; never approves payroll data."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.shift_matching import match_shift_by_clock


def shift_window(work_date: date, pattern: dict) -> tuple[datetime, datetime]:
    start_time = pattern.get("start_time")
    end_time = pattern.get("end_time")
    if not isinstance(start_time, time) or not isinstance(end_time, time):
        raise ValueError("shift_pattern_requires_start_and_end_times")
    start = datetime.combine(work_date, start_time)
    end = datetime.combine(work_date, end_time)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def work_date_for_punch(punch_at: datetime, shift_start: time) -> date:
    """Assign a midnight-crossing punch to the date on which its shift started."""
    observed = punch_at.time()
    if observed < shift_start and (shift_start.hour * 60 + shift_start.minute
                                   - observed.hour * 60 - observed.minute) > 12 * 60:
        return punch_at.date() - timedelta(days=1)
    if observed > shift_start and (observed.hour * 60 + observed.minute
                                   - shift_start.hour * 60 - shift_start.minute) > 12 * 60:
        return punch_at.date() + timedelta(days=1)
    return punch_at.date()


def infer_non_security_shift(first_clock_in: datetime, patterns: list[dict]) -> dict:
    """Use only the first observed clock-in and configured department patterns."""
    matched = match_shift_by_clock(first_clock_in, patterns)
    if matched["status"] != "matched":
        return {
            "status": "needs_review",
            "shift": None,
            "work_date": None,
            "reason": matched["reason"],
        }
    pattern = matched["shift"]
    return {
        "status": "ready_for_review",
        "shift": pattern,
        "work_date": work_date_for_punch(first_clock_in, pattern["start_time"]),
        "reason": None,
    }


def reconcile_attendance_day(
    *,
    work_date: date,
    shift_pattern: dict,
    program_clock_in: datetime | None,
    program_clock_out: datetime | None,
    fingerprint_punches: list[datetime],
) -> dict:
    """Prepare a daily comparison, preserving both sources and surfacing differences.

    The program report is primary. Fingerprint evidence fills missing endpoints only.
    A disagreement is not silently resolved; HR receives both values for review.
    """
    scheduled_start, scheduled_end = shift_window(work_date, shift_pattern)
    evidence_start = scheduled_start - timedelta(minutes=120)
    evidence_end = scheduled_end + timedelta(minutes=120)
    relevant = sorted(
        punch for punch in fingerprint_punches
        if evidence_start <= punch <= evidence_end
    )
    first_fingerprint = relevant[0] if relevant else None
    last_fingerprint = relevant[-1] if len(relevant) >= 2 else None

    selected_in = program_clock_in or first_fingerprint
    selected_out = program_clock_out or last_fingerprint
    issues = []
    if len(relevant) != len(set(relevant)):
        issues.append("duplicate_fingerprint_events")
    if len(relevant) == 1 and (program_clock_in is None or program_clock_out is None):
        issues.append("only_one_fingerprint_event")
    if not relevant and (program_clock_in is None or program_clock_out is None):
        issues.append("missing_fingerprint_evidence")
    if program_clock_in and first_fingerprint and program_clock_in != first_fingerprint:
        issues.append("program_fingerprint_clock_in_mismatch")
    if program_clock_out and last_fingerprint and program_clock_out != last_fingerprint:
        issues.append("program_fingerprint_clock_out_mismatch")

    if selected_out is not None and selected_in is not None and selected_out <= selected_in:
        if scheduled_end.date() > scheduled_start.date() and selected_out.date() == work_date:
            selected_out = selected_out + timedelta(days=1)
        else:
            issues.append("clock_out_not_after_clock_in")

    worked_minutes = None
    if selected_in is not None and selected_out is not None and selected_out > selected_in:
        worked_minutes = int((selected_out - selected_in).total_seconds() // 60)
    else:
        issues.append("incomplete_attendance_endpoints")

    return {
        "work_date": work_date,
        "shift_pattern_id": shift_pattern.get("id"),
        "scheduled_start_at": scheduled_start,
        "scheduled_end_at": scheduled_end,
        "program_clock_in": program_clock_in,
        "program_clock_out": program_clock_out,
        "first_clock_in": first_fingerprint,
        "last_clock_out": last_fingerprint,
        "selected_clock_in": selected_in,
        "selected_clock_out": selected_out,
        "worked_minutes": worked_minutes,
        "issues": sorted(set(issues)),
        "status": "ready_for_review" if not issues else "needs_review",
    }


def resolve_security_shift(employee_id: int, work_date: date, assignment: dict | None, patterns: list[dict]) -> dict:
    """Use the approved Security roster assignment; never infer Security from a punch."""
    if assignment is None:
        return {
            "status": "needs_review", "shift": None, "work_date": work_date,
            "reason": "security_roster_assignment_missing",
        }
    if assignment.get("employee_id") != employee_id or assignment.get("work_date") != work_date:
        return {
            "status": "needs_review", "shift": None, "work_date": work_date,
            "reason": "security_roster_assignment_identity_mismatch",
        }
    pattern_id = assignment.get("shift_pattern_id")
    matches = [item for item in patterns if item.get("id") == pattern_id]
    if len(matches) != 1:
        return {
            "status": "needs_review", "shift": None, "work_date": work_date,
            "reason": "security_roster_shift_pattern_missing_or_ambiguous",
        }
    return {
        "status": "ready_for_review",
        "shift": matches[0],
        "work_date": work_date,
        "reason": None,
    }


def build_monthly_reconciliation_proposals(
    *,
    employees: list[dict],
    period_start: date,
    period_end: date,
    program_events: list[dict],
    fingerprint_events: list[dict],
    patterns_by_department: dict[int, list[dict]],
    security_assignments: list[dict],
    security_department_id: int,
    security_approved_dates: set[date],
) -> list[dict]:
    """Build one review proposal per employee/work date without approving any day."""
    if period_end < period_start:
        raise ValueError("attendance_period_is_invalid")

    program_by_day = {}
    for event in program_events:
        punch_at = event.get("punch_at")
        employee_id = event.get("employee_id")
        if not isinstance(punch_at, datetime) or employee_id is None:
            continue
        key = (employee_id, punch_at.date())
        program_by_day.setdefault(key, []).append(event)

    punches_by_employee = {}
    for event in fingerprint_events:
        punch_at = event.get("punch_at")
        employee_id = event.get("employee_id")
        if isinstance(punch_at, datetime) and employee_id is not None:
            punches_by_employee.setdefault(employee_id, []).append(punch_at)
    for punches in punches_by_employee.values():
        punches.sort()

    assignments = {
        (item["employee_id"], item["work_date"]): item
        for item in security_assignments
    }
    patterns_by_id = {
        pattern["id"]: pattern
        for patterns in patterns_by_department.values()
        for pattern in patterns
    }

    calendar_days = []
    current = period_start
    while current <= period_end:
        calendar_days.append(current)
        current += timedelta(days=1)

    proposals = []
    for employee in employees:
        employee_id = employee["id"]
        department_id = employee["department_id"]
        all_punches = punches_by_employee.get(employee_id, [])
        non_security_candidates = {}
        non_security_unmatched_dates = set()

        if department_id != security_department_id:
            by_calendar_date = {}
            for punch in all_punches:
                by_calendar_date.setdefault(punch.date(), []).append(punch)
            patterns = patterns_by_department.get(department_id, [])
            for calendar_date, day_punches in by_calendar_date.items():
                first_result = infer_non_security_shift(day_punches[0], patterns)
                if first_result["status"] != "ready_for_review":
                    # A calendar day's first event can be the OUT (or an
                    # intermediate punch) of the previous day's overnight shift.
                    # If every event fits that already identified shift window,
                    # it is evidence for the previous work date, not an unmatched day.
                    previous_candidate = non_security_candidates.get(
                        calendar_date - timedelta(days=1)
                    )
                    previous_shift = (
                        previous_candidate.get("shift")
                        if previous_candidate and not previous_candidate.get("conflict")
                        else None
                    )
                    belongs_to_previous_shift = False
                    if previous_shift:
                        previous_start, previous_end = shift_window(
                            calendar_date - timedelta(days=1), previous_shift
                        )
                        # Only a shift that actually crosses midnight may own
                        # next-calendar-day punches. A 15:00–23:00 shift must
                        # never absorb the following day's 00:xx events.
                        if previous_end.date() > previous_start.date():
                            previous_window_start = previous_start - timedelta(minutes=120)
                            previous_window_end = previous_end + timedelta(minutes=120)
                            belongs_to_previous_shift = all(
                                previous_window_start <= punch <= previous_window_end
                                for punch in day_punches
                            )
                    if not belongs_to_previous_shift:
                        non_security_unmatched_dates.add(calendar_date)
                    continue
                work_date = first_result["work_date"]
                if period_start <= work_date <= period_end:
                    candidate = non_security_candidates.get(work_date)
                    if candidate and candidate["shift"].get("id") != first_result["shift"].get("id"):
                        non_security_candidates[work_date] = {
                            "conflict": True, "shift": None,
                        }
                    else:
                        non_security_candidates[work_date] = {
                            "conflict": False, "shift": first_result["shift"],
                        }

        for work_date in calendar_days:
            service_periods = employee.get("service_periods")
            if service_periods:
                in_service = any(
                    period["start_date"] <= work_date
                    and (period.get("end_date") is None or work_date <= period["end_date"])
                    for period in service_periods
                )
                if not in_service:
                    continue
            elif employee.get("hire_date") and work_date < employee["hire_date"]:
                continue
            calendar_program_day = program_by_day.get((employee_id, work_date), [])
            program_day = calendar_program_day
            program_ins = [
                item["punch_at"] for item in program_day
                if str(item.get("event_type", "")).casefold() in {"in", "clock in", "check in"}
            ]
            program_outs = [
                item["punch_at"] for item in program_day
                if str(item.get("event_type", "")).casefold() in {"out", "clock out", "check out"}
            ]
            program_in = min(program_ins) if program_ins else None
            program_out = max(program_outs) if program_outs else None

            security_assignment = None
            if department_id == security_department_id:
                security_assignment = assignments.get((employee_id, work_date))
                if security_assignment is None and work_date in security_approved_dates:
                    # An approved Security roster with no assignment means OFF for this employee.
                    continue
                pattern = patterns_by_id.get(
                    security_assignment.get("shift_pattern_id")
                ) if security_assignment else None
                if security_assignment and pattern is None:
                    proposal = {
                        "work_date": work_date, "shift_pattern_id": None,
                        "scheduled_start_at": None, "scheduled_end_at": None,
                        "program_clock_in": program_in, "program_clock_out": program_out,
                        "first_clock_in": None, "last_clock_out": None,
                        "worked_minutes": None, "issues": ["security_roster_shift_pattern_missing"],
                        "status": "needs_review",
                    }
                    proposals.append({
                        "employee_id": employee_id, "proposal": proposal,
                        "security_shift_assignment_id": security_assignment.get("id"),
                    })
                    continue
            else:
                candidate = non_security_candidates.get(work_date)
                if candidate and candidate.get("conflict"):
                    proposal = {
                        "work_date": work_date, "shift_pattern_id": None,
                        "scheduled_start_at": None, "scheduled_end_at": None,
                        "program_clock_in": program_in, "program_clock_out": program_out,
                        "first_clock_in": None, "last_clock_out": None,
                        "worked_minutes": None, "issues": ["conflicting_first_punch_shift_proposals"],
                        "status": "needs_review",
                    }
                    proposals.append({"employee_id": employee_id, "proposal": proposal})
                    continue
                pattern = candidate.get("shift") if candidate else None

            if pattern is None:
                previous_candidate = non_security_candidates.get(work_date - timedelta(days=1))
                previous_shift = (
                    previous_candidate.get("shift")
                    if previous_candidate and not previous_candidate.get("conflict")
                    else None
                )
                previous_shift_punches = []
                if previous_shift:
                    previous_start, previous_end = shift_window(
                        work_date - timedelta(days=1), previous_shift
                    )
                    if previous_end.date() > previous_start.date():
                        previous_shift_punches = [
                            punch for punch in all_punches
                            if previous_start - timedelta(minutes=120)
                            <= punch <= previous_end + timedelta(minutes=120)
                        ]
                unassigned_fingerprint_punches = [
                    punch for punch in all_punches
                    if punch not in previous_shift_punches
                ]
                unmatched_fingerprint = (
                    department_id != security_department_id
                    and (
                        work_date in non_security_unmatched_dates
                        or work_date + timedelta(days=1) in non_security_unmatched_dates
                    )
                    and not previous_shift_punches
                )
                has_fingerprint_evidence = any(
                    work_date <= punch.date() <= work_date + timedelta(days=1)
                    for punch in unassigned_fingerprint_punches
                )
                # A missing row in both source files is not proof of an absence:
                # without an assigned schedule, do not manufacture a daily record.
                # A next-calendar-day OUT event may already belong to the
                # previous day's overnight shift. Do not create a duplicate
                # review row for that same event.
                previous_candidate = non_security_candidates.get(work_date - timedelta(days=1))
                previous_shift = (
                    previous_candidate.get("shift")
                    if previous_candidate and not previous_candidate.get("conflict")
                    else None
                )
                previous_day_events = []
                previous_shift_window = None
                if previous_shift:
                    previous_start, previous_end = shift_window(
                        work_date - timedelta(days=1), previous_shift
                    )
                    if previous_end.date() > previous_start.date():
                        previous_shift_window = (
                            previous_start - timedelta(minutes=120),
                            previous_end + timedelta(minutes=120),
                        )
                    previous_day_events = [
                        item for item in calendar_program_day
                        if previous_shift_window
                        and previous_shift_window[0]
                        <= item["punch_at"]
                        <= previous_shift_window[1]
                    ]
                unassigned_program_day = [
                    item for item in calendar_program_day
                    if item not in previous_day_events
                ]
                unassigned_fingerprint = [
                    punch for punch in all_punches
                    if work_date <= punch.date() <= work_date + timedelta(days=1)
                    and not (
                        previous_shift_window
                        and previous_shift_window[0] <= punch <= previous_shift_window[1]
                    )
                ]
                if not unassigned_program_day and not unmatched_fingerprint and not unassigned_fingerprint:
                    continue
                program_day = unassigned_program_day
                program_ins = [
                    item["punch_at"] for item in program_day
                    if str(item.get("event_type", "")).casefold() in {"in", "clock in", "check in"}
                ]
                program_outs = [
                    item["punch_at"] for item in program_day
                    if str(item.get("event_type", "")).casefold() in {"out", "clock out", "check out"}
                ]
                program_in = min(program_ins) if program_ins else None
                program_out = max(program_outs) if program_outs else None
                issue = (
                    "security_roster_assignment_missing"
                    if department_id == security_department_id
                    else "fingerprint_shift_inference_missing"
                    if program_day
                    else "fingerprint_shift_unmatched_or_ambiguous"
                )
                proposal = {
                    "work_date": work_date, "shift_pattern_id": None,
                    "scheduled_start_at": None, "scheduled_end_at": None,
                    "program_clock_in": program_in, "program_clock_out": program_out,
                    "first_clock_in": None, "last_clock_out": None,
                    "worked_minutes": None,
                    "issues": [issue],
                    "status": "needs_review",
                }
                proposals.append({"employee_id": employee_id, "proposal": proposal})
                continue

            scheduled_start, scheduled_end = shift_window(work_date, pattern)
            evidence_start = scheduled_start - timedelta(minutes=120)
            evidence_end = scheduled_end + timedelta(minutes=120)
            # Group program-report IN/OUT events by the scheduled shift window,
            # not by calendar date, so an overnight OUT remains on shift start day.
            program_day = [
                item for item in program_events
                if item.get("employee_id") == employee_id
                and isinstance(item.get("punch_at"), datetime)
                and evidence_start <= item["punch_at"] <= evidence_end
                and (
                    scheduled_end.date() > scheduled_start.date()
                    or item["punch_at"].date() == work_date
                )
            ]
            program_ins = [
                item["punch_at"] for item in program_day
                if str(item.get("event_type", "")).casefold() in {"in", "clock in", "check in"}
            ]
            program_outs = [
                item["punch_at"] for item in program_day
                if str(item.get("event_type", "")).casefold() in {"out", "clock out", "check out"}
            ]
            program_in = min(program_ins) if program_ins else None
            program_out = max(program_outs) if program_outs else None
            day_fingerprint = [
                punch for punch in all_punches
                if evidence_start <= punch <= evidence_end
                and (
                    scheduled_end.date() > scheduled_start.date()
                    or punch.date() == work_date
                )
            ]
            proposal = reconcile_attendance_day(
                work_date=work_date,
                shift_pattern=pattern,
                program_clock_in=program_in,
                program_clock_out=program_out,
                fingerprint_punches=day_fingerprint,
            )
            # Multiple same-direction program events cannot be safely reduced
            # to min(IN)/max(OUT): preserve the proposal but require HR review.
            if len(program_ins) > 1:
                proposal["issues"] = sorted(set(proposal["issues"] + ["multiple_program_clock_in_events"]))
            if len(program_outs) > 1:
                proposal["issues"] = sorted(set(proposal["issues"] + ["multiple_program_clock_out_events"]))
            if len(set(item["punch_at"] for item in program_day)) != len(program_day):
                proposal["issues"] = sorted(set(proposal["issues"] + ["duplicate_program_events"]))
            if proposal["issues"]:
                proposal["status"] = "needs_review"
            proposals.append({
                "employee_id": employee_id,
                "proposal": proposal,
                "security_shift_assignment_id": (
                    security_assignment.get("id") if security_assignment else None
                ),
            })
    return proposals
