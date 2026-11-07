"""Build Security shift proposals from configured roster and shift-pattern rules.

Security team size is flexible. At least two active employees must be configured,
and at least two guards must work each day. Two working guards use 12-hour
patterns; three use 8-hour patterns. Larger daily teams require configured patterns.
OFF is not a shift.
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


def _minutes(value):
    parsed = _as_time(value)
    return parsed.hour * 60 + parsed.minute


def _interval_segments(start, end):
    start_minute = _minutes(start)
    end_minute = _minutes(end)
    if start_minute == end_minute:
        return None
    if end_minute > start_minute:
        return [(start_minute, end_minute)]
    return [(start_minute, 24 * 60), (0, end_minute)]


def _intervals_overlap(first, second):
    return any(
        max(a, c) < min(b, d)
        for a, b in first
        for c, d in second
    )


def build_security_day_proposal(roster, shift_patterns):
    """Return a reviewable proposal without inferring unconfigured durations.

    At least two employees must be present in the roster and at least two guards
    must work each day. Two working guards require 12-hour patterns; three
    require 8-hour patterns. Larger daily teams must match configured patterns.
    Ambiguity, missing patterns, duplicate starts, and overlaps block approval.
    """
    if not isinstance(roster, dict) or len(roster) < 2:
        return {
            "status": "needs_review",
            "assignments": [],
            "issues": ["security_roster_must_contain_at_least_two_employees"],
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
    if len(working) < 2:
        return {
            "status": "needs_review",
            "assignments": [],
            "issues": ["daily_security_working_count_must_be_at_least_two"],
        }

    starts = [start for _, start in working]
    if len(set(starts)) != len(starts):
        return {
            "status": "needs_review",
            "assignments": [],
            "issues": ["duplicate_security_shift_start_times"],
        }

    assignments = []
    if len(working) in (2, 3):
        duration = 12 if len(working) == 2 else 8
        for employee_id, start in working:
            end = _plus_hours(start, duration)
            matches = [
                pattern for pattern in shift_patterns
                if _pattern_time(pattern["start_time"]) == start
                and _pattern_time(pattern["end_time"]) == end
            ]
            if len(matches) != 1:
                kind = "missing" if not matches else "ambiguous"
                issues.append(
                    f"shift_pattern_resolution_{kind}:{employee_id}:"
                    f"{start.isoformat(timespec='minutes')}-"
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
    else:
        for employee_id, start in working:
            matches = [
                pattern for pattern in shift_patterns
                if _pattern_time(pattern["start_time"]) == start
            ]
            if len(matches) != 1:
                kind = "missing" if not matches else "ambiguous"
                issues.append(
                    f"shift_pattern_resolution_{kind}:{employee_id}:"
                    f"{start.isoformat(timespec='minutes')}"
                )
                continue
            pattern = matches[0]
            end = _pattern_time(pattern["end_time"])
            if _interval_segments(start, end) is None:
                issues.append(f"invalid_zero_length_shift_pattern:{employee_id}")
                continue
            duration_minutes = (_minutes(end) - _minutes(start)) % (24 * 60)
            assignments.append({
                "employee_id": employee_id,
                "start_time": start,
                "end_time": end,
                "duration_hours": duration_minutes / 60,
                "shift_pattern_id": matches[0]["id"],
            })

    intervals = {}
    for item in assignments:
        segments = _interval_segments(item["start_time"], item["end_time"])
        if segments is None:
            issues.append(f"invalid_zero_length_shift_pattern:{item['employee_id']}")
        else:
            intervals[item["employee_id"]] = segments

    employee_ids = list(intervals)
    for index, employee_id in enumerate(employee_ids):
        for other_id in employee_ids[index + 1:]:
            if _intervals_overlap(intervals[employee_id], intervals[other_id]):
                issues.append(f"overlapping_security_shifts:{employee_id}:{other_id}")

    return {
        "status": "ready_for_admin_review" if not issues else "needs_review",
        "assignments": assignments,
        "issues": issues,
    }
