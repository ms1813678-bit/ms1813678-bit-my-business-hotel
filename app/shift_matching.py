"""Conservative shift matching using bounded, non-overlapping clock windows.

This module only proposes a shift from a clock-in timestamp. It never changes
attendance timestamps and deliberately returns ambiguity for duplicate start
times (for example, the two Security shifts that both start at 08:00).
"""
from datetime import datetime, time

MAX_MATCH_MINUTES = 120
_DAY_MINUTES = 24 * 60


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _signed_delta(observed: int, start: int) -> int:
    return (observed - start + 720) % _DAY_MINUTES - 720


def _effective_bounds(pattern: dict, starts: list[int]) -> tuple[int, int]:
    start = _minutes(pattern["start_time"])
    before = pattern.get("match_before_minutes")
    after = pattern.get("match_after_minutes")
    if before is None or after is None:
        raise ValueError("Both matching margins must be configured.")
    if not 0 <= before <= MAX_MATCH_MINUTES or not 0 <= after <= MAX_MATCH_MINUTES:
        raise ValueError("Matching margins must be between 0 and 120 minutes.")

    # Duplicate starts are intentionally not split by the matcher.
    distinct = sorted(set(starts))
    index = distinct.index(start)
    previous_start = distinct[index - 1]
    next_start = distinct[(index + 1) % len(distinct)]
    previous_gap = (start - previous_start) % _DAY_MINUTES
    next_gap = (next_start - start) % _DAY_MINUTES

    # Exclude the exact midpoint minute for even gaps, so adjacent windows
    # can never overlap. For odd gaps, this gives the two sides distinct minutes.
    effective_before = min(before, MAX_MATCH_MINUTES, max(0, (previous_gap - 1) // 2))
    effective_after = min(after, MAX_MATCH_MINUTES, max(0, (next_gap - 1) // 2))
    return effective_before, effective_after


def match_shift_by_clock(clock_in: datetime, patterns: list[dict]) -> dict:
    """Return matched/unmatched/ambiguous without guessing.

    Each pattern contains id, name, start_time, match_before_minutes and
    match_after_minutes. The legacy match_window_minutes field is ignored.
    """
    if not patterns:
        return {"status": "unmatched", "shift": None, "reason": "no_active_shift_patterns"}

    starts = []
    for pattern in patterns:
        start = pattern.get("start_time")
        if start is None:
            continue
        # Every active shift start constrains neighboring windows, even if its
        # own margins are not configured. This prevents a configured shift
        # from claiming clock-ins that could belong to an unconfigured shift.
        starts.append(_minutes(start))
        before = pattern.get("match_before_minutes")
        after = pattern.get("match_after_minutes")
        if before is not None and not 0 <= before <= MAX_MATCH_MINUTES:
            raise ValueError("Matching margins must be between 0 and 120 minutes.")
        if after is not None and not 0 <= after <= MAX_MATCH_MINUTES:
            raise ValueError("Matching margins must be between 0 and 120 minutes.")

    if not starts:
        return {"status": "unmatched", "shift": None, "reason": "matching_margins_not_configured"}

    observed = _minutes(clock_in.time())
    matches = []
    for pattern in patterns:
        start_time = pattern.get("start_time")
        before = pattern.get("match_before_minutes")
        after = pattern.get("match_after_minutes")
        if start_time is None or before is None or after is None:
            continue
        effective_before, effective_after = _effective_bounds(pattern, starts)
        delta = _signed_delta(observed, _minutes(start_time))
        if -effective_before <= delta <= effective_after:
            matches.append(pattern)

    if not matches:
        return {"status": "unmatched", "shift": None, "reason": "outside_non_overlapping_matching_window"}
    if len(matches) > 1:
        return {"status": "ambiguous", "shift": None, "reason": "duplicate_or_indistinguishable_shift_start_times"}
    return {"status": "matched", "shift": matches[0], "reason": None}
