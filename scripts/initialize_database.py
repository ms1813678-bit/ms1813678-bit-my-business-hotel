"""Apply versioned database migrations from any working directory."""
from pathlib import Path

from alembic import command
from alembic.config import Config

from app import models  # noqa: F401 — register all ORM tables
from app.db import engine


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def initialize_database(database_engine=engine) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))

    with database_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def main() -> None:
    initialize_database()
    print("Database migrations applied successfully.")


if __name__ == "__main__":
    main()
