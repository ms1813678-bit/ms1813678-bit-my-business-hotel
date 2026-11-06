from datetime import datetime, time

from app.shift_matching import match_shift_by_clock


def pattern(identifier, start, window=30):
    return {
        "id": identifier,
        "name": identifier,
        "start_time": time.fromisoformat(start),
        "match_window_minutes": window,
    }


def test_matches_clock_inside_configured_window():
    result = match_shift_by_clock(
        datetime(2026, 11, 1, 7, 12),
        [pattern("reception-morning", "07:00", 20)],
    )
    assert result["status"] == "matched"
    assert result["shift"]["id"] == "reception-morning"


def test_clock_outside_window_is_not_guessed():
    result = match_shift_by_clock(
        datetime(2026, 11, 1, 7, 31),
        [pattern("reception-morning", "07:00", 30)],
    )
    assert result["status"] == "unmatched"
    assert result["shift"] is None


def test_overnight_shift_matches_after_midnight_by_clock():
    result = match_shift_by_clock(
        datetime(2026, 11, 2, 0, 5),
        [pattern("reception-night", "23:00", 90)],
    )
    assert result["status"] == "matched"
    assert result["shift"]["id"] == "reception-night"


def test_overlapping_shift_windows_are_sent_for_manual_review():
    result = match_shift_by_clock(
        datetime(2026, 11, 1, 14, 58),
        [
            pattern("reception-morning", "07:00", 500),
            pattern("reception-evening", "15:00", 30),
        ],
    )
    assert result["status"] == "ambiguous"
    assert result["shift"] is None


def test_unconfigured_window_disables_automatic_matching():
    result = match_shift_by_clock(
        datetime(2026, 11, 1, 9, 0),
        [pattern("administration", "09:00", None)],
    )
    assert result["status"] == "unmatched"


def test_negative_window_is_rejected():
    try:
        match_shift_by_clock(
            datetime(2026, 11, 1, 9, 0),
            [pattern("invalid", "09:00", -1)],
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Negative matching window must be rejected.")
