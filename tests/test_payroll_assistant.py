from decimal import Decimal

import pytest

from app.payroll_assistant import (
    analyze_payroll_rows,
    explain_payroll_change,
    interpret_local_command,
    payroll_totals,
)


def sample_row(**updates):
    row = {
        "employee_code_snapshot": "MY-TEST-0001",
        "employee_name_snapshot": "Synthetic Employee",
        "base_salary": "9000.00",
        "attendance_bonus": "0",
        "performance_bonus": "0",
        "occupancy_bonus": "0",
        "overtime_amount": "0",
        "other_bonus": "0",
        "staff_reward_amount": "0",
        "absence_deduction": "0",
        "lateness_deduction": "0",
        "insurance_share": "0",
        "taxes": "0",
        "other_deductions": "0",
        "advance_deduction": "500",
        "staff_sanction_amount": "0",
        "net_pay": "8500.00",
    }
    row.update(updates)
    return row


def test_payroll_totals_are_read_only_and_reconcile():
    row = sample_row()
    assert payroll_totals(row) == {
        "gross": Decimal("9000.00"),
        "deductions": Decimal("500.00"),
        "net": Decimal("8500.00"),
    }
    assert row["net_pay"] == "8500.00"


def test_totals_use_same_half_up_currency_rounding_as_payroll_math():
    row = sample_row(base_salary="0.005", advance_deduction="0", net_pay="0.01")
    assert payroll_totals(row)["gross"] == Decimal("0.01")
    assert payroll_totals(row)["net"] == Decimal("0.01")


def test_analyzer_detects_duplicate_employee_and_deduction_source():
    row = sample_row(deduction_source_ids=["advance-1", "advance-1"])
    findings = analyze_payroll_rows([row, sample_row()])
    codes = {finding["code"] for finding in findings}
    assert "duplicate_employee_row" in codes
    assert "duplicate_deduction_source" in codes


def test_analyzer_detects_net_mismatch_and_unapproved_overtime():
    row = sample_row(net_pay="8999", overtime_hours="2", overtime_approved=False)
    codes = {finding["code"] for finding in analyze_payroll_rows([row])}
    assert "net_total_mismatch" in codes
    assert "unapproved_overtime" in codes


@pytest.mark.parametrize("approval", [None, False])
def test_analyzer_treats_missing_overtime_approval_as_unapproved(approval):
    row = sample_row(overtime_hours="2")
    if approval is not None:
        row["overtime_approved"] = approval
    codes = {finding["code"] for finding in analyze_payroll_rows([row])}
    assert "unapproved_overtime" in codes


def test_analyzer_accepts_explicit_overtime_approval():
    row = sample_row(overtime_hours="2", overtime_approved=True)
    codes = {finding["code"] for finding in analyze_payroll_rows([row])}
    assert "unapproved_overtime" not in codes


def test_analyzer_flags_missing_identity_and_negative_amount():
    row = sample_row(employee_code_snapshot="", employee_name_snapshot="", taxes="-5")
    codes = {finding["code"] for finding in analyze_payroll_rows([row])}
    assert "missing_employee_identity" in codes
    assert "negative_line_item" in codes


@pytest.mark.parametrize("invalid", ["not-a-number", "NaN", "Infinity"])
def test_analyzer_reports_invalid_or_non_finite_amounts(invalid):
    codes = {finding["code"] for finding in analyze_payroll_rows([sample_row(taxes=invalid)])}
    assert "invalid_amount" in codes


def test_analyzer_detects_deductions_exceeding_earnings():
    row = sample_row(advance_deduction="9500", net_pay="-500")
    codes = {finding["code"] for finding in analyze_payroll_rows([row])}
    assert "deductions_exceed_earnings" in codes
    assert "net_total_mismatch" not in codes


def test_change_explanation_lists_only_changed_components():
    before = sample_row()
    after = sample_row(performance_bonus="250", advance_deduction="300", net_pay="8950")
    result = explain_payroll_change(before, after)
    assert result["change_count"] == 3
    assert {item["field"] for item in result["changes"]} == {
        "performance_bonus", "advance_deduction", "net_pay"
    }


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("قارن راتب الشهر الحالي بالسابق", "compare_payroll"),
        ("راجع الخصومات المكررة", "review_deductions"),
        ("حلل القيم غير الطبيعية", "analyze_payroll"),
        ("اعرض ملخص الرواتب", "payroll_summary"),
        ("", "help"),
    ],
)
def test_local_command_intent_arabic_and_english(message, expected):
    assert interpret_local_command(message) == expected

@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("compare this month with last month", "compare_payroll"),
        ("review duplicate deductions", "review_deductions"),
        ("check payroll anomalies", "analyze_payroll"),
        ("show payroll summary", "payroll_summary"),
        ("unknown request", "help"),
    ],
)
def test_local_command_intent_english(message, expected):
    assert interpret_local_command(message) == expected
