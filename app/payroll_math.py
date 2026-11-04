from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def hourly_rate(basic_salary: Decimal, daily_hours: Decimal) -> Decimal:
    if basic_salary < 0 or daily_hours <= 0:
        raise ValueError("Salary must be non-negative and daily hours must be positive.")
    return basic_salary / (daily_hours * Decimal(30))


def lateness_deduction(
    basic_salary: Decimal,
    daily_hours: Decimal,
    late_minutes: int = 0,
    early_minutes: int = 0,
    late_grace_minutes: int = 10,
    early_grace_minutes: int = 5,
) -> Decimal:
    if min(late_minutes, early_minutes, late_grace_minutes, early_grace_minutes) < 0:
        raise ValueError("Minutes cannot be negative.")
    chargeable = max(0, late_minutes - late_grace_minutes)
    chargeable += max(0, early_minutes - early_grace_minutes)
    hours = Decimal(chargeable) / Decimal(60)
    return money(hourly_rate(basic_salary, daily_hours) * hours)


def absence_deduction(basic_salary: Decimal, year: int, month: int, absence_days: int) -> Decimal:
    days_in_month = monthrange(year, month)[1]
    if basic_salary < 0 or absence_days < 0 or absence_days > days_in_month:
        raise ValueError("Invalid salary or absence days.")
    return money(basic_salary / Decimal(days_in_month) * Decimal(absence_days))


def prorated_new_hire_salary(basic_salary: Decimal, hire_date: date) -> Decimal:
    days_in_month = monthrange(hire_date.year, hire_date.month)[1]
    payable_days = days_in_month - hire_date.day + 1
    if basic_salary < 0:
        raise ValueError("Salary cannot be negative.")
    return money(basic_salary / Decimal(days_in_month) * Decimal(payable_days))
