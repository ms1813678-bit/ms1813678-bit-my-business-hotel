"""Read-only comparison of approved security roster shifts vs fingerprint punches.

This module never writes to the database, never approves attendance, and never
touches payroll. It only builds a review report for the HR administrator.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select

from app.attendance_reconciliation import shift_window, work_date_for_punch
from app.db import SessionLocal
from app.models import (
    AttendancePunch,
    Department,
    Employee,
    SecurityShiftAssignment,
    ShiftPattern,
)


_DATE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")


def parse_work_date_from_question(question: str, default: date | None = None) -> date:
    """Extract YYYY-MM-DD from the question, otherwise use default or today."""
    match = _DATE_RE.search(question or "")
    if match:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return default or date.today()


def is_security_comparison_request(question: str) -> bool:
    """Detect natural-language requests to compare security roster with punches."""
    q = " ".join((question or "").casefold().split())
    if not q:
        return False
    security_terms = ("أمن", "الامن", "الأمن", "حراس", "حارس", "security")
    compare_terms = (
        "قارن", "مقارنة", "مطابقة", "بصم", "بصمة", "fingerprint",
        "جدول", "وردية", "ورديات", "حضور الأمن", "حضور الامن",
    )
    has_security = any(term in q for term in security_terms)
    has_compare = any(term in q for term in compare_terms)
    return has_security and has_compare


def _minutes_between(first: datetime, second: datetime) -> int:
    return int((second - first).total_seconds() // 60)


def compare_one_assignment(
    *,
    employee_id: int,
    employee_code: str,
    employee_name: str,
    work_date: date,
    shift_start: time,
    shift_end: time,
    shift_pattern_id: int | None,
    fingerprint_punches: list[datetime],
    margin_minutes: int = 120,
) -> dict[str, Any]:
    """Compare one assigned security shift with fingerprint evidence for that day."""
    pattern = {"id": shift_pattern_id, "start_time": shift_start, "end_time": shift_end}
    scheduled_start, scheduled_end = shift_window(work_date, pattern)
    evidence_start = scheduled_start - timedelta(minutes=margin_minutes)
    evidence_end = scheduled_end + timedelta(minutes=margin_minutes)

    relevant = sorted(
        punch for punch in fingerprint_punches
        if evidence_start <= punch <= evidence_end
    )
    unique_relevant = sorted(set(relevant))
    mismatches: list[dict[str, Any]] = []

    base = {
        "employee_id": employee_id,
        "employee_code": employee_code,
        "employee_name": employee_name,
        "work_date": work_date.isoformat(),
        "scheduled_start": scheduled_start.isoformat(sep=" ", timespec="minutes"),
        "scheduled_end": scheduled_end.isoformat(sep=" ", timespec="minutes"),
        "shift_pattern_id": shift_pattern_id,
        "actual_punches": [item.isoformat(sep=" ", timespec="minutes") for item in unique_relevant],
    }

    if len(relevant) != len(set(relevant)):
        mismatches.append({
            **base,
            "mismatch_type": "duplicate_punches",
            "difference_minutes": None,
            "detail": "توجد بصمات مكررة في نافذة الوردية.",
        })

    if not unique_relevant:
        mismatches.append({
            **base,
            "mismatch_type": "absence_despite_assigned_shift",
            "difference_minutes": None,
            "detail": "لا توجد أي بصمة رغم وجود وردية معتمدة.",
        })
        return {
            "employee_id": employee_id,
            "employee_code": employee_code,
            "employee_name": employee_name,
            "work_date": work_date,
            "status": "needs_review",
            "mismatches": mismatches,
            "first_punch": None,
            "last_punch": None,
        }

    first_punch = unique_relevant[0]
    last_punch = unique_relevant[-1] if len(unique_relevant) >= 2 else None

    if last_punch is None:
        mismatches.append({
            **base,
            "mismatch_type": "missing_departure_punch",
            "difference_minutes": None,
            "detail": "يوجد دخول بدون بصمة انصراف واضحة.",
        })
    else:
        late_minutes = _minutes_between(scheduled_start, first_punch)
        if late_minutes > 0:
            mismatches.append({
                **base,
                "mismatch_type": "late_arrival",
                "difference_minutes": late_minutes,
                "detail": f"تأخر عن بداية الوردية بمقدار {late_minutes} دقيقة.",
            })
        early_minutes = _minutes_between(last_punch, scheduled_end)
        if early_minutes > 0:
            mismatches.append({
                **base,
                "mismatch_type": "early_departure",
                "difference_minutes": early_minutes,
                "detail": f"انصراف مبكر عن نهاية الوردية بمقدار {early_minutes} دقيقة.",
            })
        worked = _minutes_between(first_punch, last_punch)
        scheduled = _minutes_between(scheduled_start, scheduled_end)
        if scheduled > 0 and abs(worked - scheduled) >= 60:
            mismatches.append({
                **base,
                "mismatch_type": "unexpected_duration",
                "difference_minutes": worked - scheduled,
                "detail": (
                    f"مدة العمل الفعلية {worked} دقيقة مقابل المدة المجدولة {scheduled} دقيقة."
                ),
            })

    outside = [
        punch for punch in fingerprint_punches
        if not (evidence_start <= punch <= evidence_end)
        and work_date_for_punch(punch, shift_start) == work_date
    ]
    if outside and unique_relevant:
        mismatches.append({
            **base,
            "mismatch_type": "wrong_shift_association",
            "difference_minutes": None,
            "detail": "توجد بصمات في نفس يوم العمل خارج نافذة الوردية المعتمدة.",
        })

    return {
        "employee_id": employee_id,
        "employee_code": employee_code,
        "employee_name": employee_name,
        "work_date": work_date,
        "status": "needs_review" if mismatches else "matched",
        "mismatches": mismatches,
        "first_punch": first_punch,
        "last_punch": last_punch,
    }


def detect_off_day_punches(
    *,
    employee_id: int,
    employee_code: str,
    employee_name: str,
    work_date: date,
    fingerprint_punches: list[datetime],
    assigned_employee_ids: set[int],
) -> list[dict[str, Any]]:
    """Flag fingerprint activity on a day when the guard has no approved assignment."""
    if employee_id in assigned_employee_ids:
        return []
    day_punches = [
        punch for punch in fingerprint_punches
        if punch.date() == work_date or punch.date() == work_date + timedelta(days=1)
    ]
    if not day_punches:
        return []
    return [{
        "employee_id": employee_id,
        "employee_code": employee_code,
        "employee_name": employee_name,
        "work_date": work_date.isoformat(),
        "scheduled_start": None,
        "scheduled_end": None,
        "shift_pattern_id": None,
        "actual_punches": [
            item.isoformat(sep=" ", timespec="minutes") for item in sorted(set(day_punches))
        ],
        "mismatch_type": "punch_on_off_day",
        "difference_minutes": None,
        "detail": "ظهرت بصمات في يوم غير مجدول لهذا الحارس (OFF).",
    }]


def build_security_fingerprint_comparison_report(
    *,
    work_date: date,
    assignments: list[dict[str, Any]],
    security_employees: list[dict[str, Any]],
    fingerprint_punches_by_employee: dict[int, list[datetime]],
    margin_minutes: int = 120,
) -> dict[str, Any]:
    """Build a full-day review report without mutating any records."""
    mismatches: list[dict[str, Any]] = []
    matched_count = 0
    assigned_ids = {item["employee_id"] for item in assignments}

    for item in assignments:
        employee_id = item["employee_id"]
        result = compare_one_assignment(
            employee_id=employee_id,
            employee_code=item.get("employee_code") or "",
            employee_name=item.get("employee_name") or "",
            work_date=work_date,
            shift_start=item["start_time"],
            shift_end=item["end_time"],
            shift_pattern_id=item.get("shift_pattern_id"),
            fingerprint_punches=fingerprint_punches_by_employee.get(employee_id, []),
            margin_minutes=margin_minutes,
        )
        if result["status"] == "matched":
            matched_count += 1
        mismatches.extend(result["mismatches"])

    for employee in security_employees:
        mismatches.extend(
            detect_off_day_punches(
                employee_id=employee["id"],
                employee_code=employee.get("code") or "",
                employee_name=employee.get("full_name") or "",
                work_date=work_date,
                fingerprint_punches=fingerprint_punches_by_employee.get(employee["id"], []),
                assigned_employee_ids=assigned_ids,
            )
        )

    return {
        "work_date": work_date.isoformat(),
        "assigned_count": len(assignments),
        "matched_count": matched_count,
        "mismatch_count": len(mismatches),
        "status": "needs_review" if mismatches else "matched",
        "mismatches": mismatches,
        "note": (
            "هذا تقرير مراجعة فقط. لم يتم اعتماد حضور أو خصم راتب أو تعديل الجدول."
        ),
    }


def load_security_fingerprint_comparison(work_date: date) -> dict[str, Any]:
    """Load approved security assignments and fingerprint punches for one work date."""
    with SessionLocal() as db:
        department = db.scalar(
            select(Department).where(Department.name == "Security", Department.active.is_(True))
        )
        if department is None:
            return {
                "work_date": work_date.isoformat(),
                "assigned_count": 0,
                "matched_count": 0,
                "mismatch_count": 0,
                "status": "needs_review",
                "mismatches": [],
                "note": "قسم الأمن غير مُعرَّف في النظام.",
            }

        employees = db.scalars(
            select(Employee).where(
                Employee.department_id == department.id,
                Employee.status == "active",
            ).order_by(Employee.id)
        ).all()
        security_employees = [
            {"id": item.id, "code": item.code, "full_name": item.full_name}
            for item in employees
        ]
        employee_by_id = {item.id: item for item in employees}

        assignments_rows = db.scalars(
            select(SecurityShiftAssignment).where(
                SecurityShiftAssignment.work_date == work_date,
                SecurityShiftAssignment.employee_id.in_([item.id for item in employees] or [-1]),
            )
        ).all()
        patterns = {
            item.id: item
            for item in db.scalars(
                select(ShiftPattern).where(
                    ShiftPattern.department_id == department.id,
                    ShiftPattern.active.is_(True),
                )
            ).all()
        }

        assignments: list[dict[str, Any]] = []
        for row in assignments_rows:
            pattern = patterns.get(row.shift_pattern_id)
            if pattern is None:
                continue
            employee = employee_by_id.get(row.employee_id)
            if employee is None:
                continue
            assignments.append({
                "employee_id": employee.id,
                "employee_code": employee.code,
                "employee_name": employee.full_name,
                "start_time": pattern.start_time,
                "end_time": pattern.end_time,
                "shift_pattern_id": pattern.id,
            })

        window_start = datetime.combine(work_date - timedelta(days=1), time(0, 0))
        window_end = datetime.combine(work_date + timedelta(days=2), time(0, 0))
        punches = db.scalars(
            select(AttendancePunch).where(
                AttendancePunch.employee_id.in_([item.id for item in employees] or [-1]),
                AttendancePunch.punch_at.is_not(None),
                AttendancePunch.punch_at >= window_start,
                AttendancePunch.punch_at < window_end,
            ).order_by(AttendancePunch.punch_at)
        ).all()
        by_employee: dict[int, list[datetime]] = {}
        for punch in punches:
            if punch.employee_id is None or punch.punch_at is None:
                continue
            by_employee.setdefault(punch.employee_id, []).append(punch.punch_at)

        if not assignments:
            return {
                "work_date": work_date.isoformat(),
                "assigned_count": 0,
                "matched_count": 0,
                "mismatch_count": 0,
                "status": "needs_review",
                "mismatches": [],
                "note": (
                    f"لا يوجد جدول أمن معتمد لتاريخ {work_date.isoformat()}. "
                    "ارفع جدول الأمن واعتمده أولًا، ثم أعد المقارنة."
                ),
            }

        return build_security_fingerprint_comparison_report(
            work_date=work_date,
            assignments=assignments,
            security_employees=security_employees,
            fingerprint_punches_by_employee=by_employee,
        )


def format_security_comparison_report_ar(report: dict[str, Any]) -> str:
    """Render the comparison report in clear Arabic for the administrator."""
    lines = [
        f"تقرير مقارنة جدول الأمن مع البصمة لتاريخ {report.get('work_date')}.",
        report.get("note") or "",
        (
            f"الورديات المعتمدة: {report.get('assigned_count', 0)} — "
            f"المتطابقة: {report.get('matched_count', 0)} — "
            f"الملاحظات: {report.get('mismatch_count', 0)}."
        ),
    ]
    mismatches = report.get("mismatches") or []
    if not mismatches:
        lines.append("لا توجد فروقات ظاهرة في البيانات المتاحة لهذا اليوم.")
        return "\n".join(line for line in lines if line)

    type_labels = {
        "absence_despite_assigned_shift": "غياب رغم وجود وردية",
        "missing_departure_punch": "نقص بصمة انصراف",
        "late_arrival": "تأخر حضور",
        "early_departure": "انصراف مبكر",
        "duplicate_punches": "بصمات مكررة",
        "unexpected_duration": "مدة غير متوقعة",
        "wrong_shift_association": "ارتباط وردية غير متوقع",
        "punch_on_off_day": "بصمة في يوم OFF",
    }
    lines.append("تفاصيل الملاحظات (للمراجعة فقط):")
    for index, item in enumerate(mismatches, start=1):
        label = type_labels.get(item.get("mismatch_type"), item.get("mismatch_type"))
        diff = item.get("difference_minutes")
        diff_text = f" — الفرق: {diff} دقيقة" if diff is not None else ""
        lines.append(
            f"{index}) {item.get('employee_code')} — {item.get('employee_name')} — "
            f"{label}{diff_text}."
        )
        if item.get("scheduled_start") and item.get("scheduled_end"):
            lines.append(
                f"   الوردية: {item['scheduled_start']} → {item['scheduled_end']}"
            )
        punches = item.get("actual_punches") or []
        if punches:
            lines.append(f"   البصمات: {', '.join(punches)}")
        if item.get("detail"):
            lines.append(f"   {item['detail']}")
    lines.append("لم يتم اعتماد أي حضور أو تعديل راتب. راجع الملاحظات ثم قرر يدويًا.")
    return "\n".join(lines)


def answer_security_roster_comparison(question: str) -> str:
    """Entry point for the AI assistant analysis path."""
    work_date = parse_work_date_from_question(question)
    report = load_security_fingerprint_comparison(work_date)
    return format_security_comparison_report_ar(report)
