from datetime import date
from decimal import Decimal

from app.advance_repayment_schedule import build_repayment_schedule


def test_one_time_advance_belongs_to_request_month():
    schedule = build_repayment_schedule(Decimal("1000"), date(2026, 10, 19), 1)
    assert schedule == [{
        "installment_number": 1,
        "payroll_month": date(2026, 10, 1),
        "pay_date": date(2026, 11, 5),
        "amount": Decimal("1000.00"),
    }]


def test_six_thousand_advance_is_six_monthly_installments():
    schedule = build_repayment_schedule(Decimal("6000"), date(2026, 10, 19), 6)
    assert [row["payroll_month"] for row in schedule] == [
        date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1),
        date(2027, 1, 1), date(2027, 2, 1), date(2027, 3, 1),
    ]
    assert [row["pay_date"] for row in schedule] == [
        date(2026, 11, 5), date(2026, 12, 5), date(2027, 1, 5),
        date(2027, 2, 5), date(2027, 3, 5), date(2027, 4, 5),
    ]
    assert sum((row["amount"] for row in schedule), Decimal("0")) == Decimal("6000.00")


def test_fractional_cent_remainder_is_preserved():
    schedule = build_repayment_schedule(Decimal("10.00"), date(2026, 10, 19), 3)
    assert [row["amount"] for row in schedule] == [
        Decimal("3.34"), Decimal("3.33"), Decimal("3.33")
    ]


def test_installment_count_is_bounded():
    for count in (0, 37):
        try:
            build_repayment_schedule(Decimal("100"), date(2026, 10, 19), count)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid installment count was accepted")
