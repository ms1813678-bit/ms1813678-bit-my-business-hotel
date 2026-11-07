from datetime import date

from scripts.generate_monthly_report import previous_month_period


def test_previous_month_period_handles_year_boundary():
    assert previous_month_period(date(2027, 1, 1)) == (
        date(2026, 12, 1),
        date(2026, 12, 31),
    )


def test_previous_month_period_handles_leap_february():
    assert previous_month_period(date(2024, 3, 1)) == (
        date(2024, 2, 1),
        date(2024, 2, 29),
    )


def test_previous_month_period_handles_30_day_month():
    assert previous_month_period(date(2026, 5, 1)) == (
        date(2026, 4, 1),
        date(2026, 4, 30),
    )
