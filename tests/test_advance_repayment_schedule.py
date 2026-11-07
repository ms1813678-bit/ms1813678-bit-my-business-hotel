from datetime import date
from decimal import Decimal

import pytest

from app.advance_repayment_schedule import build_repayment_schedule
from app.advance_workflow import payroll_month_for_advance


def test_six_installments_start_on_fifth_of_following_month():
    schedule = build_repayment_schedule(
        Decimal("6000.00"), date(2026, 9, 15), installment_count=6
    )
    assert [row["amount"] for row in schedule] == [Decimal("1000.00")] * 6
    assert [row["pay_date"] for row in schedule] == [
        date(2026, 10, 5),
        date(2026, 11, 5),
        date(2026, 12, 5),
        date(2027, 1, 5),
        date(2027, 2, 5),
        date(2027, 3, 5),
    ]
    assert [row["payroll_month"] for row in schedule] == [
        date(2026, 9, 1),
        date(2026, 10, 1),
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
        date(2027, 2, 1),
    ]


def test_single_deduction_is_the_full_amount():
    schedule = build_repayment_schedule(
        Decimal("1250.75"), date(2026, 12, 18), installment_count=1
    )
    assert len(schedule) == 1
    assert schedule[0]["amount"] == Decimal("1250.75")
    assert schedule[0]["payroll_month"] == date(2026, 12, 1)
    assert schedule[0]["pay_date"] == date(2027, 1, 5)


def test_remainder_is_preserved_to_the_cent():
    schedule = build_repayment_schedule(
        Decimal("100.00"), date(2026, 1, 15), installment_count=3
    )
    assert [row["amount"] for row in schedule] == [
        Decimal("33.34"), Decimal("33.33"), Decimal("33.33")
    ]
    assert sum((row["amount"] for row in schedule), Decimal("0.00")) == Decimal("100.00")


@pytest.mark.parametrize(
    "amount,count",
    [(Decimal("0"), 1), (Decimal("-1"), 1), (Decimal("10"), 0), (Decimal("10"), 37)],
)
def test_invalid_amount_or_installment_count_is_rejected(amount, count):
    with pytest.raises(ValueError):
        build_repayment_schedule(amount, date(2026, 9, 15), count)



@pytest.mark.parametrize(
    "request_date,expected_payroll_month",
    [
        (date(2026, 9, 15), date(2026, 9, 1)),
        (date(2026, 10, 19), date(2026, 10, 1)),
        (date(2026, 12, 22), date(2026, 12, 1)),
    ],
)
def test_advance_is_assigned_to_its_request_month(request_date, expected_payroll_month):
    assert payroll_month_for_advance(request_date) == expected_payroll_month
