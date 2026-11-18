"""PostgreSQL integration test for the Alembic attendance schema chain."""
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.db import engine as configured_engine


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def postgres_schema():
    if configured_engine.dialect.name != "postgresql":
        pytest.skip("These migration tests require PostgreSQL.")

    schema = "migration_test_" + uuid4().hex
    with configured_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    test_engine = create_engine(
        configured_engine.url,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    try:
        yield test_engine
    finally:
        test_engine.dispose()
        with configured_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def test_fresh_postgres_install_creates_attendance_schema(postgres_schema):
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))

    with postgres_schema.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        config.attributes.pop("connection", None)

    inspector = inspect(postgres_schema)
    tables = set(inspector.get_table_names())
    assert {
        "departments",
        "branches",
        "employees",
        "employee_service_periods",
        "shift_patterns",
        "security_shift_assignments",
        "attendance_import_batches",
        "attendance_punches",
        "attendance_daily_records",
        "attendance_import_issues",
        "attendance_months",
        "alembic_version",
    }.issubset(tables)

    columns = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in (
            "attendance_import_batches",
            "attendance_punches",
            "attendance_daily_records",
            "attendance_import_issues",
        )
    }
    assert {"source_type", "file_sha256", "original_file_bytes"}.issubset(
        columns["attendance_import_batches"]
    )
    assert {"employee_id", "punch_at", "source_sheet", "event_index"}.issubset(
        columns["attendance_punches"]
    )
    assert {"work_date", "status", "approved_by", "approved_at"}.issubset(
        columns["attendance_daily_records"]
    )
    assert {"reason", "resolved_employee_id", "resolved_by", "resolved_at"}.issubset(
        columns["attendance_import_issues"]
    )

    with postgres_schema.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0015_attendance_issue_resolution"


def test_initializer_adopts_existing_complete_orm_schema(postgres_schema):
    from app import models as _models  # noqa: F401
    from app.db import Base
    from scripts.initialize_database import initialize_database

    Base.metadata.create_all(bind=postgres_schema)
    with postgres_schema.begin() as connection:
        connection.execute(
            text("INSERT INTO departments (name, active) VALUES ('Existing department', true)")
        )

    initialize_database(postgres_schema)

    with postgres_schema.connect() as connection:
        assert connection.execute(
            text("SELECT name FROM departments WHERE name = 'Existing department'")
        ).scalar_one() == "Existing department"
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "0015_attendance_issue_resolution"
