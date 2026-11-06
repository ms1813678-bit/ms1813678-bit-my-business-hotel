from datetime import datetime, time

from app.shift_matching import match_shift_by_clock


def pattern(identifier, start, before=120, after=120, **extra):
    return {
        "id": identifier,
        "name": identifier,
        "start_time": time.fromisoformat(start),
        "match_before_minutes": before,
        "match_after_minutes": after,
        **extra,
    }


def match(hour, minute, patterns, day=1):
    return match_shift_by_clock(datetime(2026, 11, day, hour, minute), patterns)


def test_matches_inside_configured_before_and_after_margins():
    p = [pattern("admin", "09:00", 30, 45)]
    assert match(8, 30, p)["status"] == "matched"
    assert match(9, 45, p)["status"] == "matched"
    assert match(9, 46, p)["status"] == "unmatched"


def test_two_shifts_have_no_overlap_and_midpoint_is_unmatched():
    p = [pattern("morning", "07:00"), pattern("evening", "15:00")]
    assert match(10, 59, p)["shift"]["id"] == "morning"
    assert match(11, 0, p)["status"] == "unmatched"
    assert match(11, 1, p)["shift"]["id"] == "evening"


def test_duplicate_start_times_are_ambiguous_even_at_exact_start():
    p = [pattern("security-12h", "08:00"), pattern("security-8h", "08:00")]
    result = match(8, 0, p)
    assert result["status"] == "ambiguous"
    assert result["shift"] is None


def test_close_shift_starts_are_trimmed_to_prevent_overlap():
    p = [pattern("hk-08", "08:00"), pattern("hk-09", "09:00")]
    assert match(8, 29, p)["shift"]["id"] == "hk-08"
    assert match(8, 30, p)["status"] == "unmatched"
    assert match(8, 31, p)["shift"]["id"] == "hk-09"


def test_overnight_start_matches_after_midnight():
    p = [pattern("night", "23:00")]
    assert match(0, 5, p, day=2)["shift"]["id"] == "night"
    assert match(22, 0, p)["shift"]["id"] == "night"


def test_midnight_start_matches_before_midnight():
    p = [pattern("midnight", "00:00")]
    assert match(23, 55, p)["shift"]["id"] == "midnight"


def test_zero_margins_only_match_exact_start():
    p = [pattern("zero", "09:00", 0, 0)]
    assert match(9, 0, p)["status"] == "matched"
    assert match(9, 1, p)["status"] == "unmatched"


def test_legacy_field_is_not_used_by_matcher():
    p = [{
        "id": "legacy", "name": "legacy", "start_time": time(9, 0),
        "match_window_minutes": 120,
    }]
    assert match(9, 0, p)["status"] == "unmatched"


def test_invalid_margin_is_rejected():
    p = [pattern("bad", "09:00", 121, 0)]
    try:
        match(9, 0, p)
    except ValueError:
        pass
    else:
        raise AssertionError("Margins above 120 minutes must be rejected.")


def test_empty_patterns_are_unmatched():
    assert match(9, 0, [])["status"] == "unmatched"


def test_unconfigured_neighbor_still_limits_matching_window():
    configured = pattern("hk-08", "08:00")
    unconfigured = {
        "id": "hk-09", "name": "hk-09", "start_time": time(9, 0),
        "match_before_minutes": None, "match_after_minutes": None,
    }
    assert match(8, 29, [configured, unconfigured])["status"] == "matched"
    assert match(8, 30, [configured, unconfigured])["status"] == "unmatched"


def test_duplicate_start_is_ambiguous_even_if_one_margin_is_unset():
    configured = pattern("security-12h", "08:00")
    unconfigured = {
        "id": "security-8h", "name": "security-8h", "start_time": time(8, 0),
        "match_before_minutes": None, "match_after_minutes": None,
    }
    result = match(8, 0, [configured, unconfigured])
    assert result["status"] == "ambiguous"
    assert result["shift"] is None


def test_single_shift_keeps_full_configured_margins():
    p = [pattern("admin", "09:00", 120, 120)]
    assert match(7, 0, p)["status"] == "matched"
    assert match(11, 0, p)["status"] == "matched"
    assert match(11, 1, p)["status"] == "unmatched"
