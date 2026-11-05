"""Atomic, database-backed employee code allocation."""
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import EmployeeCodeCounter


def allocate_employee_code(db: Session) -> str:
    """Reserve the next never-reused MY code inside the caller's transaction."""
    number = db.execute(
        update(EmployeeCodeCounter)
        .where(EmployeeCodeCounter.id == 1)
        .values(last_number=EmployeeCodeCounter.last_number + 1)
        .returning(EmployeeCodeCounter.last_number)
    ).scalar_one_or_none()
    if number is None:
        raise RuntimeError("Employee code counter is missing; apply database migrations first.")
    return f"MY-{number:04d}"
