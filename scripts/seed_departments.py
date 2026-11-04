from sqlalchemy import select

from app.db import SessionLocal
from app.models import Department


DEFAULT_DEPARTMENTS = [
    "Reception",
    "Security",
    "Housekeeping",
    "Maintenance",
    "Administration",
    "HR",
    "Finance",
    "Purchasing",
]


def main() -> None:
    with SessionLocal() as db:
        for name in DEFAULT_DEPARTMENTS:
            existing = db.scalar(select(Department).where(Department.name == name))
            if existing is None:
                db.add(Department(name=name, active=True))
        db.commit()
    print("Default departments are ready.")


if __name__ == "__main__":
    main()
