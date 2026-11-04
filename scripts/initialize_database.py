from app.db import Base, engine
from app import models  # noqa: F401


def main() -> None:
    Base.metadata.create_all(bind=engine)
    print("Database tables created or already present.")


if __name__ == "__main__":
    main()
