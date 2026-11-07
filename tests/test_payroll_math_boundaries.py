"""Boundary regression tests for the payroll policy helpers."""
from datetime import date
from decimal import Decimal

import pytest

from app.payroll_math import (
    absence_deduction,
    lateness_deduction,
    prorated_new_hire_salary,
)
from app.payroll_workflow import payroll_period


@pytest.mark.parametrize("minutes", [-1, -5])
def test_lateness_rejects_negative_minute_inputs(minutes):
    with pytest.raises(ValueError, match="Minutes cannot be negative"):
        lateness_deduction(
            Decimal("9000"),
            Decimal("8"),
            late_minutes=minutes,
        )


def test_absence_rejects_negative_salary_and_negative_days():
    with pytest.raises(ValueError, match="Invalid salary or absence days"):
        absence_deduction(Decimal("-1"), 2026, 2, 1)
    with pytest.raises(ValueError, match="Invalid salary or absence days"):
        absence_deduction(Decimal("9000"), 2026, 2, -1)


def test_new_hire_on_last_day_receives_one_calendar_day():
    assert prorated_new_hire_salary(
        Decimal("9000"), date(2026, 10, 31)
    ) == Decimal("290.32")


@pytest.mark.parametrize("month", [0, 13])
def test_payroll_period_rejects_month_outside_calendar(month):
    with pytest.raises(ValueError, match="Invalid payroll month"):
        payroll_period(2026, month)


def test_december_payroll_is_paid_on_january_fifth_of_next_year():
    start, end, pay_date = payroll_period(2026, 12)
    assert (start, end, pay_date) == (
        date(2026, 12, 1),
        date(2026, 12, 31),
        date(2027, 1, 5),
    )
