"""Allocate employee codes from a non-transactional PostgreSQL sequence."""
from sqlalchemy import text
from sqlalchemy.orm import Session


def allocate_employee_code(db: Session) -> str:
    """Reserve the next never-reused MY code inside the caller's transaction.

    PostgreSQL sequences are intentionally non-transactional: a later rollback
    does not make a reserved number available again.
    """
    number = db.execute(
        text("SELECT nextval('employee_code_number_seq')")
    ).scalar_one()
    db.execute(
        text("""
            UPDATE employee_code_counters
            SET last_number = GREATEST(last_number, :number)
            WHERE id = 1
        """),
        {"number": number},
    )
    return f"MY-{number:04d}"
