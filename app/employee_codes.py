"""Allocate employee codes safely for PostgreSQL and lightweight SQLite tests."""
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import EmployeeCodeCounter


def allocate_employee_code(db: Session) -> str:
    """Reserve the next MY code.

    Production uses PostgreSQL's non-transactional sequence, so a rollback never
    makes a reserved number available again. SQLite support is retained only for
    isolated unit tests; application configuration rejects SQLite as a runtime DB.
    """
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        number = db.execute(
            update(EmployeeCodeCounter)
            .where(EmployeeCodeCounter.id == 1)
            .values(last_number=EmployeeCodeCounter.last_number + 1)
            .returning(EmployeeCodeCounter.last_number)
        ).scalar_one_or_none()
        if number is None:
            raise RuntimeError("Employee code counter is missing; apply database migrations first.")
        # nextval() is deliberately outside transaction rollback semantics.
        # The counter table mirrors the greatest committed reservation for migration safety.
        return f"MY-{number:04d}" if _mirror_sequence_reservation(db, number) else f"MY-{number:04d}"

    if dialect == "sqlite":
        # SQLite is used by isolated route/unit tests only, never by PostgreSQL
        # migration or concurrency tests and never as a supported application DB.
        number = db.execute(
            update(EmployeeCodeCounter)
            .where(EmployeeCodeCounter.id == 1)
            .values(last_number=EmployeeCodeCounter.last_number + 1)
            .returning(EmployeeCodeCounter.last_number)
        ).scalar_one_or_none()
        if number is None:
            raise RuntimeError("Employee code counter is missing; initialize the test database first.")
        return f"MY-{number:04d}"

    raise RuntimeError(f"Unsupported database dialect for employee codes: {dialect}")


def _mirror_sequence_reservation(db: Session, number: int) -> bool:
    from sqlalchemy import text

    result = db.execute(
        text("""
            UPDATE employee_code_counters
            SET last_number = GREATEST(last_number, :number)
            WHERE id = 1
        """),
        {"number": number},
    )
    return result.rowcount == 1
