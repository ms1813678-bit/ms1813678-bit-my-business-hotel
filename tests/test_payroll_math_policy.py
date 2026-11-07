"""Regression tests for the confirmed payroll arithmetic policy."""
from datetime import date
from decimal import Decimal

import pytest

from app.payroll_math import (
    absence_deduction,
    hourly_rate,
    lateness_deduction,
    money,
    prorated_new_hire_salary,
)


def test_hourly_rate_uses_confirmed_thirty_day_basis():
    # 9,000 / (8 daily hours * 30 days) = 37.50 EGP/hour.
    assert hourly_rate(Decimal("9000"), Decimal("8")) == Decimal("37.5")


def test_lateness_deduction_applies_per_day_grace_before_hourly_rate():
    # 20 late minutes - 10 grace + 10 early minutes - 5 grace = 15 minutes.
    # 37.50 * 15 / 60 = 9.375, rounded half-up to 9.38.
    assert lateness_deduction(
        Decimal("9000"),
        Decimal("8"),
        late_minutes=20,
        early_minutes=10,
        late_grace_minutes=10,
        early_grace_minutes=5,
    ) == Decimal("9.38")


@pytest.mark.parametrize(
    ("year", "month", "expected_daily"),
    [(2026, 2, Decimal("321.43")), (2026, 4, Decimal("300.00"))],
)
def test_absence_deduction_uses_actual_calendar_days(year, month, expected_daily):
    assert money(absence_deduction(Decimal("9000"), year, month, 1)) == expected_daily


def test_new_hire_proration_uses_actual_days_in_hire_month():
    assert prorated_new_hire_salary(Decimal("9000"), date(2026, 10, 16)) == Decimal("4645.16")


@pytest.mark.parametrize(
    ("salary", "hours", "message"),
    [
        (Decimal("-1"), Decimal("8"), "non-negative"),
        (Decimal("9000"), Decimal("0"), "positive"),
    ],
)
def test_hourly_rate_rejects_invalid_salary_or_hours(salary, hours, message):
    with pytest.raises(ValueError, match=message):
        hourly_rate(salary, hours)


def test_absence_deduction_rejects_more_days_than_month():
    with pytest.raises(ValueError, match="Invalid salary or absence days"):
        absence_deduction(Decimal("9000"), 2026, 2, 29)
