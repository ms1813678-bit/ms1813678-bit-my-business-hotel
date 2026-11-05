"""Exercise Alembic against the isolated PostgreSQL service used by CI."""
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db import engine


def test_postgresql_migration_upgrade_and_single_admin_guards():
    config = Config("alembic.ini")

    # A fresh database must migrate successfully, and repeating upgrade must be safe.
    command.upgrade(config, "head")
    command.upgrade(config, "head")

    inspector = inspect(engine)
    assert "admin_accounts" in inspector.get_table_names()
    assert "alembic_version" in inspector.get_table_names()
    assert "uq_admin_accounts_singleton_key" in {
        item["name"] for item in inspector.get_unique_constraints("admin_accounts")
    }
    assert "ck_admin_accounts_must_remain_active" in {
        item["name"] for item in inspector.get_check_constraints("admin_accounts")
    }

    # Keep all fixture writes in one outer transaction. Nested transactions let us
    # verify rejected statements without poisoning the outer transaction. The final
    # rollback removes the test administrator even when an assertion fails.
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert revision == "0001_initial"

            connection.execute(
                text(
                    "INSERT INTO admin_accounts "
                    "(singleton_key, username, password_hash, active, created_at) "
                    "VALUES (1, 'migration-test-admin', 'test-hash', TRUE, CURRENT_TIMESTAMP)"
                )
            )

            # PostgreSQL must enforce the active-account check independently of the ORM.
            try:
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE admin_accounts SET active = FALSE "
                            "WHERE username = 'migration-test-admin'"
                        )
                    )
            except IntegrityError:
                pass
            else:
                raise AssertionError(
                    "PostgreSQL allowed the only administrator to be deactivated"
                )

            # The database trigger must reject direct SQL deletion as well.
            try:
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "DELETE FROM admin_accounts "
                            "WHERE username = 'migration-test-admin'"
                        )
                    )
            except DBAPIError:
                pass
            else:
                raise AssertionError(
                    "PostgreSQL allowed the only administrator to be deleted"
                )

            remaining = connection.execute(
                text(
                    "SELECT COUNT(*) FROM admin_accounts "
                    "WHERE username = 'migration-test-admin'"
                )
            ).scalar_one()
            assert remaining == 1
        finally:
            transaction.rollback()
