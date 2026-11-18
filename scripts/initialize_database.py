"""Apply versioned database migrations from any working directory."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app import models  # noqa: F401 — register all ORM tables
from app.db import engine


def initialize_database(database_engine=engine) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))

    with database_engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(741930221, 27)"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def main() -> None:
    initialize_database()
    print("Database migrations applied successfully.")


if __name__ == "__main__":
    main()
