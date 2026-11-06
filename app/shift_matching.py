"""Pure shift matching helpers; no attendance timestamps are inferred or modified."""
from datetime import datetime, time


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def match_shift_by_clock(
    clock_in: datetime,
    patterns: list[dict],
) -> dict:
    """Return a unique shift match, or an explicit ambiguous/unmatched result.

    Each pattern contains id, name, start_time (time), and match_window_minutes.
    A missing match window deliberately disables automatic matching for that pattern.
    """
    observed = _minutes(clock_in.time())
    matches = []
    for pattern in patterns:
        window = pattern.get("match_window_minutes")
        start = pattern.get("start_time")
        if window is None or start is None:
            continue
        if window < 0:
            raise ValueError("Match window cannot be negative.")
        delta = (observed - _minutes(start) + 720) % 1440 - 720
        if abs(delta) <= window:
            matches.append((abs(delta), pattern))

    if not matches:
        return {"status": "unmatched", "shift": None, "reason": "no_shift_within_configured_window"}
    if len(matches) > 1:
        return {"status": "ambiguous", "shift": None, "reason": "multiple_shifts_within_window"}
    return {"status": "matched", "shift": matches[0][1], "reason": None}
