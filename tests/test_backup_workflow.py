"""Backup archive integrity and retention policy tests."""
import hashlib
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.backup_workflow import (
    cleanup_retention,
    create_backup,
    sha256_file,
    validate_backup_root,
    verify_backup_archive,
)
from app.db import Base
from app.models import BackupRun


def make_backup(path: Path, backup_type: str, created_at: datetime) -> Path:
    database_dump = b"synthetic PostgreSQL custom-format dump bytes"
    photo = b"synthetic employee photo bytes"
    photo_hash = hashlib.sha256()
    photo_hash.update(b"employee.jpg")
    photo_hash.update(hashlib.sha256(photo).hexdigest().encode("ascii"))
    manifest = {
        "format_version": 1,
        "backup_type": backup_type,
        "created_at": created_at.isoformat(),
        "database_sha256": hashlib.sha256(database_dump).hexdigest(),
        "employee_photos_sha256": photo_hash.hexdigest(),
        "employee_photo_count": 1,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("database.dump", database_dump)
        archive.writestr("employee_photos/employee.jpg", photo)
        archive.writestr("manifest.json", json.dumps(manifest))
    return path


def test_backup_archive_hash_and_photo_manifest_are_verified(tmp_path):
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    archive = make_backup(tmp_path / "daily" / "valid.zip", "daily", now)
    manifest = verify_backup_archive(archive, sha256_file(archive))
    assert manifest["backup_type"] == "daily"
    assert manifest["employee_photo_count"] == 1

    with pytest.raises(ValueError, match="hash"):
        verify_backup_archive(archive, "0" * 64)


def test_backup_retention_deletes_only_expired_daily_and_weekly_archives(tmp_path):
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    old_daily = make_backup(
        tmp_path / "daily" / "old-daily.zip", "daily", now - timedelta(days=41)
    )
    recent_daily = make_backup(
        tmp_path / "daily" / "recent-daily.zip", "daily", now - timedelta(days=39)
    )
    old_weekly = make_backup(
        tmp_path / "weekly" / "old-weekly.zip", "weekly", now - timedelta(days=366)
    )
    old_quarterly = make_backup(
        tmp_path / "quarterly" / "old-quarterly.zip", "quarterly", now - timedelta(days=900)
    )
    old_manual = make_backup(
        tmp_path / "manual" / "old-manual.zip", "manual", now - timedelta(days=900)
    )
    old_pre_restore = make_backup(
        tmp_path / "pre_restore" / "old-pre-restore.zip",
        "pre_restore",
        now - timedelta(days=900),
    )

    removed = cleanup_retention(tmp_path, now=now)
    assert str(old_daily) in removed
    assert str(old_weekly) in removed
    assert not old_daily.exists()
    assert not old_weekly.exists()
    assert recent_daily.exists()
    assert old_quarterly.exists()
    assert old_manual.exists()
    assert old_pre_restore.exists()


def test_backup_location_requires_an_absolute_path(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        validate_backup_root("relative/backups", require_external=False)
    valid = validate_backup_root(str(tmp_path / "external-backups"), require_external=False)
    assert valid.is_absolute()
    assert valid.is_dir()


def test_backup_without_configured_external_drive_is_recorded_as_failed(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            run = create_backup(db, "manual", "synthetic-admin")
            assert run.status == "failed"
            assert "external backup drive" in run.error_message
            db.commit()
            saved = db.scalar(select(BackupRun))
            assert saved.status == "failed"
            assert saved.archive_path is None
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
