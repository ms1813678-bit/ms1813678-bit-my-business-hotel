"""Build full-day security shift proposals from guards actually working that day.

The Excel roster supplies each employee's shift START time or OFF. OFF is excluded
before the model is chosen. Exactly two working guards use the approved 12-hour
windows (08:00–20:00 and 20:00–08:00). More than two use the three approved
8-hour windows (08:00–16:00, 16:00–00:00, 00:00–08:00). Several guards may share
one start time. A day is ready for review only when every required window is
covered and each one resolves to exactly one ShiftPattern. Nothing is persisted here.
"""
from datetime import time


_TWELVE_HOUR_WINDOWS = {
    time(8, 0): time(20, 0),
    time(20, 0): time(8, 0),
}
_EIGHT_HOUR_WINDOWS = {
    time(8, 0): time(16, 0),
    time(16, 0): time(0, 0),
    time(0, 0): time(8, 0),
}


def _as_time(value):
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    if isinstance(value, str):
        return time.fromisoformat(value.strip())
    raise ValueError("Shift start must be a time or an HH:MM[:SS] string.")


def _pattern_time(value):
    return _as_time(value)


def _needs_review(issues):
    return {"status": "needs_review", "assignments": [], "issues": issues}


def build_security_day_proposal(roster, shift_patterns):
    """Return a reviewable proposal for one date.

    roster maps every active employee ID to an HH:MM start time or "OFF".
    The shift model follows the number of guards working after OFF is excluded,
    not the size of the roster. Repeated start times are allowed. The work date
    stays the date the shift starts; this function does not move overnight work
    onto the next calendar day.
    """
    if not isinstance(roster, dict) or len(roster) < 2:
        return _needs_review(["security_roster_requires_at_least_two_active_employees"])
    if any(employee_id in (None, "") for employee_id in roster):
        return _needs_review(["security_roster_contains_invalid_employee_identity"])

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
        return _needs_review(issues)

    if len(working) < 2:
        return _needs_review(["daily_security_working_count_must_cover_full_day"])

    if len(working) == 2:
        windows = _TWELVE_HOUR_WINDOWS
        duration = 12
    else:
        windows = _EIGHT_HOUR_WINDOWS
        duration = 8

    unique_starts = {start for _, start in working}
    if unique_starts != set(windows):
        return _needs_review(["security_shifts_must_cover_24_hours_without_gaps_or_overlaps"])

    assignments = []
    for employee_id, start in working:
        end = windows[start]
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

    if issues:
        return _needs_review(issues)

    return {
        "status": "ready_for_admin_review",
        "assignments": assignments,
        "issues": [],
    }
