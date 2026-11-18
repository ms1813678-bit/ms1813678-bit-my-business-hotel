"""Permanent employee-code reservations."""
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import EmployeeCodeCounter


def reserve_employee_code(db: Session) -> str:
    """Reserve a unique code that is not reused after deletion or transaction rollback."""
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        number = db.scalar(text("SELECT nextval('employee_code_number_seq')"))
        if number is None:
            raise RuntimeError("employee_code_sequence_unavailable")
        result = db.execute(
            text(
                "UPDATE employee_code_counters "
                "SET last_number = GREATEST(last_number, :number) WHERE id = 1"
            ),
            {"number": number},
        )
        if result.rowcount != 1:
            raise RuntimeError("employee_code_counter_unavailable")
    else:
        counter = db.get(EmployeeCodeCounter, 1)
        if counter is None:
            counter = EmployeeCodeCounter(id=1, last_number=0)
            db.add(counter)
            db.flush()
        counter.last_number += 1
        number = counter.last_number
        db.flush()

    return f"MY-{number:04d}"
