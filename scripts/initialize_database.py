"""Apply the versioned schema using Alembic."""
from alembic import command
from alembic.config import Config


def main():
    command.upgrade(Config("alembic.ini"), "head")
    print("Database migrations applied successfully.")


if __name__ == "__main__":
    main()
