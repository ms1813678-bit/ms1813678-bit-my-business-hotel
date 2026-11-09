from datetime import date
from decimal import Decimal

import pytest

from app.advance_repayment_schedule import build_repayment_schedule


def test_six_installments_follow_request_month_and_next_month_pay_dates():
    schedule = build_repayment_schedule(
        Decimal("6000.00"), date(2026, 10, 19), installment_count=6
    )
    assert [row["payroll_month"] for row in schedule] == [
        date(2026, 10, 1),
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
        date(2027, 2, 1),
        date(2027, 3, 1),
    ]
    assert [row["pay_date"] for row in schedule] == [
        date(2026, 11, 5),
        date(2026, 12, 5),
        date(2027, 1, 5),
        date(2027, 2, 5),
        date(2027, 3, 5),
        date(2027, 4, 5),
    ]
    assert [row["amount"] for row in schedule] == [Decimal("1000.00")] * 6
    assert sum((row["amount"] for row in schedule), Decimal("0.00")) == Decimal("6000.00")


def test_one_time_advance_is_full_amount_in_request_month():
    schedule = build_repayment_schedule(
        Decimal("1250.75"), date(2026, 12, 18), installment_count=1
    )
    assert schedule == [{
        "installment_number": 1,
        "payroll_month": date(2026, 12, 1),
        "pay_date": date(2027, 1, 5),
        "amount": Decimal("1250.75"),
    }]


def test_remainder_is_allocated_to_earliest_installments_and_total_is_exact():
    schedule = build_repayment_schedule(
        Decimal("10.00"), date(2026, 10, 19), installment_count=3
    )
    assert [row["amount"] for row in schedule] == [
        Decimal("3.34"), Decimal("3.33"), Decimal("3.33")
    ]
    assert sum((row["amount"] for row in schedule), Decimal("0.00")) == Decimal("10.00")


@pytest.mark.parametrize(
    "amount,count",
    [
        (Decimal("0"), 1),
        (Decimal("-1"), 1),
        (Decimal("10"), 0),
        (Decimal("10"), 37),
        (Decimal("0.02"), 3),
    ],
)
def test_invalid_amount_or_installment_count_is_rejected(amount, count):
    with pytest.raises(ValueError):
        build_repayment_schedule(amount, date(2026, 10, 19), count)
