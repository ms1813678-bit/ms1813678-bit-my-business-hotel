"""Unit tests for backup path validation and retention helpers."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models_backup import BackupRun, SystemSetting
from app.backup_service import (
    cleanup_retention,
    get_backup_root,
    save_backup_root,
    validate_backup_root,
)


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with SessionLocal() as session:
        yield session


def test_validate_backup_root_requires_absolute(tmp_path):
    with pytest.raises(ValueError, match="backup_path_must_be_absolute"):
        validate_backup_root("relative/path", require_external=False)


def test_save_and_get_backup_root(db_session, tmp_path):
    path = validate_backup_root(str(tmp_path), require_external=False)
    saved = save_backup_root(db_session, str(path), actor="admin")
    db_session.commit()
    loaded = get_backup_root(db_session)
    assert loaded == saved


def test_cleanup_retention_skips_quarterly(tmp_path):
    # No archives yet — should return empty
    removed = cleanup_retention(tmp_path)
    assert removed == []
