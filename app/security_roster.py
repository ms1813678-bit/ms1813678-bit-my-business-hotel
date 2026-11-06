"""Build security shift proposals from the approved three-person daily roster rule.

The Excel roster supplies each employee's shift START time or OFF. This module
derives the duration from the number of employees working that date and resolves
the result against configured Security ShiftPattern rows. It does not persist or
approve assignments.
"""
from datetime import time


def _as_time(value):
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    if isinstance(value, str):
        return time.fromisoformat(value.strip())
    raise ValueError("Shift start must be a time or an HH:MM[:SS] string.")


def _plus_hours(start, hours):
    total = (start.hour * 60 + start.minute + hours * 60) % (24 * 60)
    return time(total // 60, total % 60)


def _pattern_time(value):
    return _as_time(value)


def build_security_day_proposal(roster, shift_patterns):
    """Return a reviewable proposal for one date.

    roster maps exactly three employee IDs to an HH:MM start time or "OFF".
    Two working employees receive 12-hour shifts; three receive 8-hour shifts.
    A proposal is ready only when every derived start/end resolves to exactly
    one configured Security ShiftPattern and all starts are distinct.
    """
    if not isinstance(roster, dict) or len(roster) != 3:
        return {
            "status": "needs_review",
            "assignments": [],
            "issues": ["security_roster_must_contain_exactly_three_employees"],
        }

    working = []
    issues = []
    for employee_id, value in roster.items():
        if isinstance(value, str) and value.strip().upper() == "OFF":
            continue
        try:
            start = _as_time(value)
        except (TypeError, ValueError):
            issues.append(f"invalid_shift_start_for_employee:{employee_id}")
            continue
        working.append((employee_id, start))

    if issues:
        return {"status": "needs_review", "assignments": [], "issues": issues}

    if len(working) not in (2, 3):
        return {
            "status": "needs_review",
            "assignments": [],
            "issues": ["daily_security_working_count_must_be_two_or_three"],
        }

    starts = [start for _, start in working]
    if len(set(starts)) != len(starts):
        return {
            "status": "needs_review",
            "assignments": [],
            "issues": ["duplicate_security_shift_start_times"],
        }

    duration = 12 if len(working) == 2 else 8
    assignments = []
    for employee_id, start in working:
        end = _plus_hours(start, duration)
        matches = [
            pattern for pattern in shift_patterns
            if _pattern_time(pattern["start_time"]) == start
            and _pattern_time(pattern["end_time"]) == end
        ]
        if len(matches) != 1:
            issues.append(
                f"shift_pattern_resolution_{'missing' if not matches else 'ambiguous'}:"
                f"{employee_id}:{start.isoformat(timespec='minutes')}-"
                f"{end.isoformat(timespec='minutes')}"
            )
            continue
        assignments.append({
            "employee_id": employee_id,
            "start_time": start,
            "end_time": end,
            "duration_hours": duration,
            "shift_pattern_id": matches[0]["id"],
        })

    return {
        "status": "ready_for_admin_review" if not issues else "needs_review",
        "assignments": assignments,
        "issues": issues,
    }
