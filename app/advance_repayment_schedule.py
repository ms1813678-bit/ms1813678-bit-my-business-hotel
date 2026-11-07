"""Deterministic repayment schedule calculations for salary advances.

This module is deliberately schema-independent. Persistence and payroll consumption
must be integrated by the owning shared-schema/payroll workstreams.
"""
from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_DOWN


CENT = Decimal("0.01")


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def build_repayment_schedule(
    amount: Decimal,
    request_date: date,
    installment_count: int = 1,
) -> list[dict[str, date | Decimal]]:
    """Build payroll-month installments, each paid on the 5th of the next month.

    A request dated 15 September therefore has its first payroll month in
    September and first deduction/pay date on 5 October.
    """
    amount = Decimal(str(amount)).quantize(CENT)
    if amount <= 0:
        raise ValueError("Advance amount must be positive.")
    if not isinstance(installment_count, int) or isinstance(installment_count, bool):
        raise ValueError("Installment count must be a whole number.")
    if installment_count < 1 or installment_count > 36:
        raise ValueError("Installment count must be between 1 and 36.")

    total_cents = int((amount / CENT).to_integral_exact())
    base_cents, remainder_cents = divmod(total_cents, installment_count)
    schedule = []
    payroll_month = date(request_date.year, request_date.month, 1)

    for index in range(installment_count):
        cents = base_cents + (1 if index < remainder_cents else 0)
        pay_month = _add_months(payroll_month, 1)
        schedule.append({
            "installment_number": index + 1,
            "payroll_month": payroll_month,
            "pay_date": date(pay_month.year, pay_month.month, 5),
            "amount": (Decimal(cents) * CENT).quantize(CENT),
        })
        payroll_month = _add_months(payroll_month, 1)

    return schedule
