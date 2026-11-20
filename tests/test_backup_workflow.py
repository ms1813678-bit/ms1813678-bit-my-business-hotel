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
    restore_backup,
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
        "application": "M.Y. Business Hotel HR & Payroll System",
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


def test_backup_archive_rejects_unsafe_duplicate_and_unexpected_members(tmp_path):
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    archive = tmp_path / "manual" / "unsafe.zip"
    archive.parent.mkdir(parents=True)
    manifest = {
        "format_version": 1,
        "application": "M.Y. Business Hotel HR & Payroll System",
        "backup_type": "manual",
        "created_at": now.isoformat(),
        "database_sha256": hashlib.sha256(b"dump").hexdigest(),
        "employee_photos_sha256": hashlib.sha256(b"").hexdigest(),
        "employee_photo_count": 0,
    }
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("database.dump", b"dump")
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("../outside.txt", b"unsafe")
    with pytest.raises(ValueError, match="unsafe"):
        verify_backup_archive(archive)


def test_backup_archive_rejects_unknown_application(tmp_path):
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    archive = tmp_path / "manual" / "foreign.zip"
    archive.parent.mkdir(parents=True)
    dump = b"dump"
    manifest = {
        "format_version": 1,
        "application": "Other Application",
        "backup_type": "manual",
        "created_at": now.isoformat(),
        "database_sha256": hashlib.sha256(dump).hexdigest(),
        "employee_photos_sha256": hashlib.sha256(b"").hexdigest(),
        "employee_photo_count": 0,
    }
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("database.dump", dump)
        zf.writestr("manifest.json", json.dumps(manifest))
    with pytest.raises(ValueError, match="unknown application"):
        verify_backup_archive(archive)


def test_restore_backup_rejects_empty_reason_before_any_restore(monkeypatch, tmp_path):
    archive = tmp_path / "manual" / "valid.zip"
    make_backup(archive, "manual", datetime(2026, 11, 7, tzinfo=timezone.utc))

    called = {"value": False}

    def should_not_run(*args, **kwargs):
        called["value"] = True
        raise AssertionError("restore must not start when reason is empty")

    monkeypatch.setattr("app.backup_workflow._restore_archive_contents", should_not_run)

    with pytest.raises(ValueError, match="restore reason"):
        restore_backup(
            archive,
            sha256_file(archive),
            actor="synthetic-admin",
            reason="   ",
        )

    assert called["value"] is False


def _prepare_restore_test_environment(monkeypatch, tmp_path, *, fail_selected_restore=False):
    from sqlalchemy.orm import sessionmaker

    import app.backup_workflow as backup_workflow
    import app.db as db_module
    from app.models import AuditLog, SystemSetting

    database_path = tmp_path / "restore-test.sqlite"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    backup_root = tmp_path / "external-backups"
    selected_archive = make_backup(
        backup_root / "manual" / "selected.zip",
        "manual",
        datetime(2026, 11, 7, tzinfo=timezone.utc),
    )
    pre_restore_archive = make_backup(
        backup_root / "pre_restore" / "pre-restore.zip",
        "pre_restore",
        datetime(2026, 11, 8, tzinfo=timezone.utc),
    )
    pre_restore_hash = sha256_file(pre_restore_archive)
    selected_hash = sha256_file(selected_archive)

    with factory.begin() as db:
        db.add(SystemSetting(
            key="backup_root",
            value=str(backup_root),
            updated_by="synthetic-admin",
        ))

    def record_pre_restore_backup(db, backup_type, actor):
        assert backup_type == "pre_restore"
        run = BackupRun(
            backup_type="pre_restore",
            status="success",
            archive_path=str(pre_restore_archive),
            sha256=pre_restore_hash,
            size_bytes=pre_restore_archive.stat().st_size,
            created_at=datetime(2026, 11, 8, tzinfo=timezone.utc),
            verified_at=datetime(2026, 11, 8, tzinfo=timezone.utc),
            initiated_by=actor,
        )
        db.add(run)
        db.flush()
        return run

    restore_calls = []

    def restore_archive(path, root, expected_sha256=None):
        path = Path(path)
        restore_calls.append(path)
        if fail_selected_restore and path == selected_archive:
            raise RuntimeError("synthetic selected restore failure")
        assert root == backup_root
        if path == pre_restore_archive:
            assert expected_sha256 == pre_restore_hash

    monkeypatch.setattr(db_module, "SessionLocal", factory)
    monkeypatch.setattr(backup_workflow, "create_backup", record_pre_restore_backup)
    monkeypatch.setattr(backup_workflow, "_restore_archive_contents", restore_archive)

    return {
        "engine": engine,
        "factory": factory,
        "backup_root": backup_root,
        "selected_archive": selected_archive,
        "selected_hash": selected_hash,
        "pre_restore_archive": pre_restore_archive,
        "restore_calls": restore_calls,
        "AuditLog": AuditLog,
    }


def test_restore_backup_records_audit_and_history_after_success(monkeypatch, tmp_path):
    context = _prepare_restore_test_environment(monkeypatch, tmp_path)
    try:
        result = restore_backup(
            context["selected_archive"],
            context["selected_hash"],
            actor="synthetic-admin",
            reason="Synthetic successful restore test",
        )

        assert context["restore_calls"] == [context["selected_archive"]]
        assert result["archive_path"] == str(context["selected_archive"])
        assert result["pre_restore_archive"] == str(context["pre_restore_archive"])
        history = context["backup_root"] / "restore_history.jsonl"
        assert history.is_file()
        assert '"reason": "Synthetic successful restore test"' in history.read_text(encoding="utf-8")

        with context["factory"]() as db:
            actions = db.scalars(select(context["AuditLog"].action)).all()
            pre_restore_runs = db.scalars(
                select(BackupRun).where(BackupRun.backup_type == "pre_restore")
            ).all()
        assert actions == ["backup_restored"]
        assert len(pre_restore_runs) == 1
    finally:
        context["engine"].dispose()


def test_restore_backup_rolls_back_after_selected_archive_failure(monkeypatch, tmp_path):
    context = _prepare_restore_test_environment(
        monkeypatch, tmp_path, fail_selected_restore=True
    )
    try:
        with pytest.raises(RuntimeError, match="previous database was restored"):
            restore_backup(
                context["selected_archive"],
                context["selected_hash"],
                actor="synthetic-admin",
                reason="Synthetic rollback test",
            )

        assert context["restore_calls"] == [
            context["selected_archive"],
            context["pre_restore_archive"],
        ]
        assert not (context["backup_root"] / "restore_history.jsonl").exists()

        with context["factory"]() as db:
            actions = db.scalars(select(context["AuditLog"].action)).all()
            pre_restore_runs = db.scalars(
                select(BackupRun).where(BackupRun.backup_type == "pre_restore")
            ).all()
        assert actions == ["backup_restore_rolled_back"]
        assert len(pre_restore_runs) == 1
        assert pre_restore_runs[0].archive_path == str(context["pre_restore_archive"])
    finally:
        context["engine"].dispose()

