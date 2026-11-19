"""Restore verified backups with mandatory pre-restore snapshot and rollback."""
from __future__ import annotations

import json
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app.backup_service import (
    _database_args,
    _find_pg_tool,
    _run,
    create_backup,
    get_backup_root,
    verify_backup_archive,
)


def _extract_verified_archive(archive_path: Path, destination: Path) -> Path:
    verify_backup_archive(archive_path)
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(archive_path, "r") as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if not str(target).startswith(str(destination_resolved)):
                raise ValueError("backup_archive_unsafe_path")
        archive.extractall(destination)
    dump_path = destination / "database.dump"
    if not dump_path.is_file():
        raise ValueError("backup_missing_database_dump")
    return dump_path


def _restore_archive_contents(archive_path: Path, backup_root: Path) -> None:
    from app.db import engine

    pg_restore = _find_pg_tool("pg_restore")
    connection_args, env = _database_args()
    with tempfile.TemporaryDirectory(prefix="myhotel_restore_", dir=str(backup_root)) as work_dir:
        work = Path(work_dir)
        dump_path = _extract_verified_archive(archive_path, work)
        _run([pg_restore, "--list", str(dump_path)], env)

        engine.dispose()
        terminate_sql = (
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(terminate_sql))
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))

        restore_command = [
            pg_restore,
            "--no-owner",
            "--no-acl",
            "--exit-on-error",
            *connection_args,
            str(dump_path),
        ]
        _run(restore_command, env)


def restore_backup(
    archive_path: str | Path,
    expected_sha256: str,
    *,
    actor: str,
    reason: str,
) -> dict:
    """Restore after a verified pre-restore backup; roll back automatically on failure."""
    from app.db import SessionLocal
    from app.models import AuditLog

    if not (reason or "").strip():
        raise ValueError("restore_reason_required")
    selected = Path(archive_path).resolve()
    manifest = verify_backup_archive(selected, expected_sha256)

    with SessionLocal() as db:
        backup_root = get_backup_root(db)
    if backup_root is None:
        raise ValueError("backup_root_not_configured")
    backup_root = backup_root.resolve()
    try:
        selected.relative_to(backup_root)
    except ValueError as exc:
        raise ValueError("archive_outside_backup_root") from exc

    with SessionLocal() as db:
        pre_restore = create_backup(db, "pre_restore", actor)
        db.commit()
        if pre_restore.status != "success" or not pre_restore.archive_path:
            raise RuntimeError("pre_restore_backup_failed")
        pre_restore_id = pre_restore.id
        pre_restore_path = Path(pre_restore.archive_path)

    try:
        _restore_archive_contents(selected, backup_root)
    except Exception as restore_error:
        try:
            _restore_archive_contents(pre_restore_path, backup_root)
            with SessionLocal() as db:
                db.add(
                    AuditLog(
                        actor=actor,
                        action="backup_restore_rolled_back",
                        entity_type="backup_run",
                        entity_id=str(pre_restore_id),
                        old_value=str(selected),
                        new_value=str(pre_restore_path),
                        reason=f"Restore failed; rolled back: {restore_error}"[:4000],
                    )
                )
                db.commit()
        except Exception as rollback_error:
            raise RuntimeError(
                f"Restore failed: {restore_error}. Rollback also failed: "
                f"{rollback_error}. Preserve pre-restore archive: {pre_restore_path}"
            ) from rollback_error
        raise RuntimeError(
            f"Restore failed; previous database restored from {pre_restore_path}: {restore_error}"
        ) from restore_error

    completed_at = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.add(
            AuditLog(
                actor=actor,
                action="backup_restored",
                entity_type="backup_archive",
                entity_id=str(selected),
                old_value=None,
                new_value=str(manifest.get("created_at")),
                reason=reason.strip(),
            )
        )
        db.commit()

    restore_log = {
        "restored_at": completed_at.isoformat(),
        "actor": actor,
        "reason": reason.strip(),
        "archive_path": str(selected),
        "archive_sha256": expected_sha256,
        "backup_created_at": manifest.get("created_at"),
        "pre_restore_archive": str(pre_restore_path),
    }
    history = backup_root / "restore_history.jsonl"
    with history.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(restore_log, ensure_ascii=False) + "\n")
    return restore_log
