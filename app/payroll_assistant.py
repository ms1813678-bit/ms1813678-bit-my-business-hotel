"""Offline, read-only payroll analysis helpers for the HR administrator.

This module deliberately has no network client and never writes to the database.
It provides deterministic checks and explanations that remain available without
an AI model or internet connection.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Mapping


CENT = Decimal("0.01")
EARNING_FIELDS = (
    "base_salary",
    "attendance_bonus",
    "performance_bonus",
    "occupancy_bonus",
    "overtime_amount",
    "other_bonus",
    "staff_reward_amount",
)
DEDUCTION_FIELDS = (
    "absence_deduction",
    "lateness_deduction",
    "insurance_share",
    "taxes",
    "other_deductions",
    "advance_deduction",
    "staff_sanction_amount",
)


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _amount(row: Mapping[str, Any], field: str) -> Decimal:
    value = row.get(field, 0)
    try:
        result = Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid monetary value in {field}.") from None
    if not result.is_finite():
        raise ValueError(f"Non-finite monetary value in {field}.")
    return result


def payroll_totals(row: Mapping[str, Any]) -> dict[str, Decimal]:
    """Calculate auditable totals from stored line items without mutating them."""
    gross = sum((_amount(row, field) for field in EARNING_FIELDS), Decimal("0"))
    deductions = sum((_amount(row, field) for field in DEDUCTION_FIELDS), Decimal("0"))
    return {
        "gross": _money(gross),
        "deductions": _money(deductions),
        "net": _money(gross - deductions),
    }


def analyze_payroll_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Return deterministic warnings; findings are advisory and never auto-correct."""
    materialized = list(rows)
    findings: list[dict[str, str]] = []
    seen_codes: set[str] = set()

    for index, row in enumerate(materialized, start=1):
        code = str(row.get("employee_code_snapshot") or "").strip()
        label = code or f"row {index}"
        if not code or not str(row.get("employee_name_snapshot") or "").strip():
            findings.append({
                "severity": "warning",
                "code": "missing_employee_identity",
                "employee": label,
                "message": "Employee code or name is missing; review before approval.",
            })
        if code and code in seen_codes:
            findings.append({
                "severity": "critical",
                "code": "duplicate_employee_row",
                "employee": code,
                "message": "More than one payroll row exists for this employee.",
            })
        if code:
            seen_codes.add(code)

        try:
            totals = payroll_totals(row)
            stored_net = _money(_amount(row, "net_pay"))
            if totals["net"] != stored_net:
                findings.append({
                    "severity": "critical",
                    "code": "net_total_mismatch",
                    "employee": label,
                    "message": (
                        f"Stored net {stored_net} differs from line-item net "
                        f"{totals['net']}."
                    ),
                })
            if totals["deductions"] > totals["gross"]:
                findings.append({
                    "severity": "critical",
                    "code": "deductions_exceed_earnings",
                    "employee": label,
                    "message": "Total deductions exceed gross earnings.",
                })
            for field in (*EARNING_FIELDS, *DEDUCTION_FIELDS):
                if _amount(row, field) < 0:
                    findings.append({
                        "severity": "critical",
                        "code": "negative_line_item",
                        "employee": label,
                        "message": f"Negative value found in {field}.",
                    })
            overtime_hours = _amount(row, "overtime_hours")
            if overtime_hours > 0 and row.get("overtime_approved") is False:
                findings.append({
                    "severity": "critical",
                    "code": "unapproved_overtime",
                    "employee": label,
                    "message": "Overtime hours are present but marked unapproved.",
                })
        except ValueError as exc:
            findings.append({
                "severity": "critical",
                "code": "invalid_amount",
                "employee": label,
                "message": str(exc),
            })

        # Only flag repeated source identifiers, never equal amounts alone.
        source_ids = row.get("deduction_source_ids") or []
        normalized_ids = [str(value) for value in source_ids if value not in (None, "")]
        if len(normalized_ids) != len(set(normalized_ids)):
            findings.append({
                "severity": "critical",
                "code": "duplicate_deduction_source",
                "employee": label,
                "message": "A deduction source is linked more than once.",
            })

    return findings


def explain_payroll_change(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare two employee payroll snapshots and explain each changed component."""
    fields = (*EARNING_FIELDS, *DEDUCTION_FIELDS, "net_pay")
    changes = []
    for field in fields:
        before = _amount(previous, field)
        after = _amount(current, field)
        delta = _money(after - before)
        if delta:
            changes.append({
                "field": field,
                "previous": _money(before),
                "current": _money(after),
                "difference": delta,
            })
    return {"changes": changes, "change_count": len(changes)}


def interpret_local_command(message: str) -> str:
    """Classify common Arabic/English HR requests without sending text externally."""
    text = " ".join((message or "").casefold().split())
    if not text:
        return "help"
    if any(word in text for word in ("مقارنة", "قارن", "compare", "difference")):
        return "compare_payroll"
    if any(word in text for word in ("خصم", "خصومات", "deduction", "deductions")):
        return "review_deductions"
    if any(word in text for word in ("شذوذ", "غير طبيعي", "مشكلة", "anomaly", "anomalies", "check")):
        return "analyze_payroll"
    if any(word in text for word in ("ملخص", "إجمالي", "اجمالي", "summary", "total", "report")):
        return "payroll_summary"
    return "help"
