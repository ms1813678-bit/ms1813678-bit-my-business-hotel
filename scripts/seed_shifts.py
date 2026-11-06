"""Seed approved shift patterns and initialize untouched rows with bounded margins."""
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Department, ShiftPattern


DEFAULT_SHIFTS = {
    "Administration": [("ADMIN-0900", "إدارية 09:00–17:00", "09:00", "17:00")],
    "Reception": [
        ("REC-0700", "استقبال 07:00–15:00", "07:00", "15:00"),
        ("REC-1500", "استقبال 15:00–23:00", "15:00", "23:00"),
        ("REC-2300", "استقبال 23:00–07:00", "23:00", "07:00"),
    ],
    "Security": [
        ("SEC-0800-2000", "أمن 08:00–20:00", "08:00", "20:00"),
        ("SEC-2000-0800", "أمن 20:00–08:00", "20:00", "08:00"),
        ("SEC-0800-1600", "أمن 08:00–16:00", "08:00", "16:00"),
        ("SEC-1600-0000", "أمن 16:00–00:00", "16:00", "00:00"),
        ("SEC-0000-0800", "أمن 00:00–08:00", "00:00", "08:00"),
    ],
    "Housekeeping": [
        ("HK-0800-1600", "هاوس كيبنج 08:00–16:00", "08:00", "16:00"),
        ("HK-1600-0000", "هاوس كيبنج 16:00–00:00", "16:00", "00:00"),
        ("HK-0900-1700", "هاوس كيبنج 09:00–17:00", "09:00", "17:00"),
    ],
    "Maintenance": [("MAINT-0900", "صيانة 09:00–18:00", "09:00", "18:00")],
}


def main() -> None:
    from datetime import time

    with SessionLocal.begin() as db:
        departments = {
            department.name: department
            for department in db.scalars(
                select(Department).where(Department.name.in_(DEFAULT_SHIFTS))
            )
        }
        missing_departments = sorted(set(DEFAULT_SHIFTS) - set(departments))
        if missing_departments:
            raise RuntimeError(
                "Cannot seed shift patterns; create these departments first: "
                + ", ".join(missing_departments)
            )

        for department_name, shifts in DEFAULT_SHIFTS.items():
            department = departments[department_name]
            for code, name, start, end in shifts:
                existing = db.scalar(
                    select(ShiftPattern).where(
                        ShiftPattern.department_id == department.id,
                        ShiftPattern.code == code,
                    )
                )
                if existing is None:
                    db.add(
                        ShiftPattern(
                            department_id=department.id,
                            code=code,
                            name=name,
                            start_time=time.fromisoformat(start),
                            end_time=time.fromisoformat(end),
                            match_before_minutes=120,
                            match_after_minutes=120,
                            active=True,
                        )
                    )
                elif (
                    existing.match_before_minutes is None
                    and existing.match_after_minutes is None
                    and existing.match_window_minutes is None
                ):
                    # Apply the approved default only to untouched seeded rows;
                    # preserve any explicit HR configuration or legacy window.
                    existing.match_before_minutes = 120
                    existing.match_after_minutes = 120
    print("Default shift patterns are ready. Before/after matching margins default to the approved 120-minute maximum and are trimmed to prevent overlap.")


if __name__ == "__main__":
    main()
