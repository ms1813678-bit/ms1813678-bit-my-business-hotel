from datetime import date
from decimal import Decimal

from app.payroll_math import (
    absence_deduction,
    hourly_rate,
    lateness_deduction,
    prorated_new_hire_salary,
)


def test_hourly_rate_uses_daily_hours_times_30():
    assert hourly_rate(Decimal("9000"), Decimal("8")) == Decimal("37.5")


def test_lateness_applies_grace_and_actual_minutes():
    # 20 late minutes -> 10 chargeable; 10 early -> 5 chargeable.
    result = lateness_deduction(Decimal("9000"), Decimal("8"), 20, 10)
    assert result == Decimal("9.38")


def test_absence_uses_actual_calendar_month_length():
    assert absence_deduction(Decimal("9000"), 2026, 2, 1) == Decimal("321.43")


def test_new_hire_salary_includes_hire_date():
    assert prorated_new_hire_salary(Decimal("9000"), date(2026, 2, 28)) == Decimal("321.43")
