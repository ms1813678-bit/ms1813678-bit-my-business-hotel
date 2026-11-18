"""Apply versioned database migrations, adopting complete legacy ORM schemas safely."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app import models  # noqa: F401 — register all ORM tables
from app.db import Base, engine


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def initialize_database(database_engine=engine) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))

    with database_engine.begin() as connection:
        config.attributes["connection"] = connection
        inspector = inspect(connection)
        existing_tables = set(inspector.get_table_names())
        expected_tables = set(Base.metadata.tables)

        # Older releases used create_all(), which already created the complete
        # current ORM schema. Record that schema as current instead of replaying
        # migrations that would try to create the same tables and constraints.
        if (
            "alembic_version" not in existing_tables
            and expected_tables.issubset(existing_tables)
        ):
            command.stamp(config, "head")
        else:
            command.upgrade(config, "head")


def main() -> None:
    initialize_database()
    print("Database migrations applied successfully.")


if __name__ == "__main__":
    main()
