"""Build full-day security shift proposals from the active team size.

The Excel roster supplies each employee's shift START time or OFF. Two active
employees use two 12-hour shifts; teams of three or more use three 8-hour shifts.
Proposals must cover a complete day without gaps or overlaps and are never persisted here.
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

    roster maps every active employee ID to an HH:MM start time or "OFF".
    A two-person active team receives two 12-hour shifts; larger teams use
    three 8-hour shifts per day. Every shift must resolve to one configured
    Security ShiftPattern and the day must be covered without gaps or overlaps.
    """
    if not isinstance(roster, dict) or len(roster) < 2:
        return {
            "status": "needs_review",
            "assignments": [],
            "issues": ["security_roster_requires_at_least_two_active_employees"],
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

    starts = [start for _, start in working]
    if len(set(starts)) != len(starts):
        return {
            "status": "needs_review",
            "assignments": [],
            "issues": ["duplicate_security_shift_start_times"],
        }

    expected_working = 2 if len(roster) == 2 else 3
    if len(working) != expected_working:
        return {
            "status": "needs_review",
            "assignments": [],
            "issues": ["daily_security_working_count_must_cover_full_day"],
        }

    duration = 12 if len(roster) == 2 else 8
    ordered = sorted(working, key=lambda item: item[1])
    for index, (_employee_id, start) in enumerate(ordered):
        next_start = ordered[(index + 1) % len(ordered)][1]
        if _plus_hours(start, duration) != next_start:
            return {
                "status": "needs_review",
                "assignments": [],
                "issues": ["security_shifts_must_cover_24_hours_without_gaps_or_overlaps"],
            }

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
