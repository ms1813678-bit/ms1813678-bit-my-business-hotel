"""PostgreSQL-only migration, data-preservation, and sequence integration tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db import engine as configured_engine
from app.employee_codes import allocate_employee_code


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def postgres_schema():
    if configured_engine.dialect.name != "postgresql":
        pytest.skip("These migration tests require a real PostgreSQL database.")

    schema = "migration_test_" + uuid4().hex
    with configured_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    test_engine = create_engine(
        configured_engine.url,
        connect_args={"options": f"-csearch_path={schema}"},
        pool_size=12,
        max_overflow=4,
    )
    try:
        yield test_engine
    finally:
        test_engine.dispose()
        with configured_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def migrate(test_engine, revision):
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    with test_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)
        config.attributes.pop("connection", None)


def downgrade(test_engine, revision):
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    with test_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, revision)
        config.attributes.pop("connection", None)


def insert_employee(connection, code, name, status):
    department_id = connection.execute(
        text("SELECT id FROM departments WHERE name = 'Legacy HR'")
    ).scalar_one_or_none()
    if department_id is None:
        department_id = connection.execute(
            text("INSERT INTO departments (name, active) VALUES ('Legacy HR', TRUE) RETURNING id")
        ).scalar_one()
    connection.execute(
        text("""
            INSERT INTO employees
                (code, full_name, department_id, position, hire_date, status, basic_salary, daily_hours)
            VALUES
                (:code, :name, :department_id, 'Test position', DATE '2020-01-02',
                 :status, 5000, 8)
        """),
        {"code": code, "name": name, "department_id": department_id, "status": status},
    )


def test_fresh_postgres_install_repeated_upgrade_rollback_and_admin_guards(postgres_schema):
    test_engine = postgres_schema

    # Build a fresh schema through the base/platform migrations first.
    migrate(test_engine, "0002_platform_state")

    inspector = inspect(test_engine)
    assert {
        "departments",
        "branches",
        "employees",
        "employee_changes",
        "audit_logs",
        "admin_accounts",
        "employee_code_counters",
        "alembic_version",
    }.issubset(set(inspector.get_table_names()))

    with test_engine.begin() as connection:
        insert_employee(connection, "MY-0009", "Active Legacy", "active")
        insert_employee(connection, "MY-0007", "Former Legacy", "terminated")
        assert connection.execute(
            text("SELECT last_number FROM employee_code_counters WHERE id = 1")
        ).scalar_one() == 0
        # Seed a counter reservation as if it had been committed by an older version.
        connection.execute(
            text("UPDATE employee_code_counters SET last_number = 12 WHERE id = 1")
        )

    # The remaining migrations backfill active employees and establish the sequence.
    migrate(test_engine, "head")
    migrate(test_engine, "head")
    with test_engine.connect() as connection:
        assert "employee_service_periods" in inspect(connection).get_table_names()
        assert connection.execute(
            text("SELECT to_regclass('employee_code_number_seq') IS NOT NULL")
        ).scalar_one() is True
        assert connection.execute(
            text("SELECT COUNT(*) FROM employee_service_periods")
        ).scalar_one() == 1
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text("""
                    INSERT INTO employee_service_periods
                        (employee_id, start_date, end_date, start_reason)
                    SELECT id, DATE '2020-01-02', NULL, 'Duplicate open period'
                    FROM employees WHERE code = 'MY-0009'
                """))
        assert connection.execute(
            text("""
                SELECT e.code, p.start_date, p.end_date
                FROM employee_service_periods p
                JOIN employees e ON e.id = p.employee_id
            """)
        ).one() == ("MY-0009", date(2020, 1, 2), None)

    # Sequence reservations survive transaction rollback and cannot be reused.
    factory = sessionmaker(bind=test_engine)
    rolled_back_code = None
    with factory() as session:
        rolled_back_code = allocate_employee_code(session)
        session.rollback()
    with factory.begin() as session:
        next_code = allocate_employee_code(session)
    assert rolled_back_code == "MY-0013"
    assert next_code == "MY-0014"

    # Concurrent reservations are unique even when many transactions run together.
    def reserve(_):
        with factory.begin() as session:
            return allocate_employee_code(session)

    with ThreadPoolExecutor(max_workers=8) as executor:
        codes = list(executor.map(reserve, range(24)))
    assert len(codes) == 24
    assert len(set(codes)) == 24

    # Database constraints and trigger protect the only administrator without ORM help.
    with test_engine.begin() as connection:
        connection.execute(
            text("""
                INSERT INTO admin_accounts (username, password_hash, active)
                VALUES ('migration-admin', 'test-hash', TRUE)
            """)
        )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    text("""
                        INSERT INTO admin_accounts (username, password_hash, active)
                        VALUES ('second-admin', 'test-hash', TRUE)
                    """)
                )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    text("UPDATE admin_accounts SET active = FALSE WHERE username = 'migration-admin'")
                )
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                connection.execute(
                    text("DELETE FROM admin_accounts WHERE username = 'migration-admin'")
                )
        assert connection.execute(
            text("SELECT COUNT(*) FROM admin_accounts WHERE username = 'migration-admin'")
        ).scalar_one() == 1

    # Downgrade and re-upgrade retain employee data, history, counter, and sequence state.
    downgrade(test_engine, "base")
    with test_engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM employee_service_periods")
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT last_number FROM employee_code_counters WHERE id = 1")
        ).scalar_one() == 38
    migrate(test_engine, "head")
    with factory.begin() as session:
        after_reupgrade = allocate_employee_code(session)
    assert after_reupgrade == "MY-0039"


def test_legacy_postgres_database_upgrade_preserves_rows_and_seeds_highest_code(postgres_schema):
    test_engine = postgres_schema

    # Model a pre-Alembic database: existing HR tables and one admin, without
    # singleton_key/session_version, counter, service periods, or Alembic version.
    with test_engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE departments (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL UNIQUE,
                active BOOLEAN NOT NULL DEFAULT TRUE
            )
        """))
        connection.execute(text("""
            CREATE TABLE branches (
                id SERIAL PRIMARY KEY,
                department_id INTEGER NOT NULL REFERENCES departments(id),
                name VARCHAR(100) NOT NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                CONSTRAINT uq_branches_department_name UNIQUE (department_id, name)
            )
        """))
        connection.execute(text("""
            CREATE TABLE employees (
                id SERIAL PRIMARY KEY,
                code VARCHAR(20) NOT NULL UNIQUE,
                full_name VARCHAR(200) NOT NULL,
                national_id VARCHAR(20) UNIQUE,
                phone VARCHAR(30),
                address TEXT,
                fingerprint_id VARCHAR(50) UNIQUE,
                department_id INTEGER NOT NULL REFERENCES departments(id),
                branch_id INTEGER REFERENCES branches(id),
                position VARCHAR(120) NOT NULL,
                hire_date DATE NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'active',
                basic_salary NUMERIC(12, 2) NOT NULL,
                daily_hours NUMERIC(5, 2) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("""
            CREATE TABLE admin_accounts (
                id SERIAL PRIMARY KEY,
                username VARCHAR(80) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        connection.execute(
            text("INSERT INTO departments (name) VALUES ('Legacy HR') RETURNING id")
        )
        department_id = connection.execute(
            text("SELECT id FROM departments WHERE name = 'Legacy HR'")
        ).scalar_one()
        connection.execute(
            text("""
                INSERT INTO employees
                    (code, full_name, department_id, position, hire_date, status, basic_salary, daily_hours)
                VALUES
                    ('MY-0048', 'Existing Active', :department_id, 'HR', DATE '2019-05-06',
                     'active', 7000, 8),
                    ('MY-0020', 'Existing Former', :department_id, 'Former HR', DATE '2017-02-01',
                     'terminated', 6000, 8)
            """),
            {"department_id": department_id},
        )
        connection.execute(
            text("""
                INSERT INTO admin_accounts (username, password_hash, active)
                VALUES ('legacy-admin', 'existing-hash', TRUE)
            """)
        )
        connection.execute(text("""
            CREATE TABLE employee_code_counters (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_number INTEGER NOT NULL CHECK (last_number >= 0)
            )
        """))
        connection.execute(
            text("INSERT INTO employee_code_counters (id, last_number) VALUES (1, 45)")
        )

    migrate(test_engine, "head")
    migrate(test_engine, "head")

    with test_engine.connect() as connection:
        employees = connection.execute(
            text("SELECT code, full_name, status FROM employees ORDER BY code")
        ).all()
        assert employees == [
            ("MY-0020", "Existing Former", "terminated"),
            ("MY-0048", "Existing Active", "active"),
        ]
        assert connection.execute(
            text("SELECT last_number FROM employee_code_counters WHERE id = 1")
        ).scalar_one() == 48
        assert connection.execute(
            text("SELECT session_version FROM admin_accounts WHERE username = 'legacy-admin'")
        ).scalar_one() == 0
        periods = connection.execute(
            text("""
                SELECT e.code, p.start_date, p.end_date
                FROM employee_service_periods p
                JOIN employees e ON e.id = p.employee_id
            """)
        ).all()
        assert periods == [("MY-0048", date(2019, 5, 6), None)]

    factory = sessionmaker(bind=test_engine)
    with factory.begin() as session:
        assert allocate_employee_code(session) == "MY-0049"
