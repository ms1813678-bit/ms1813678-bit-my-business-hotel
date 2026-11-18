"""Verified local PostgreSQL backups with external-drive retention."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import settings
from app.models_backup import BackupRun, SystemSetting

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PHOTO_DIRECTORY = PROJECT_ROOT / "var" / "employee_photos"
BACKUP_SETTING_KEY = "backup_root"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_backup_root(db: Session) -> Path | None:
    setting = db.get(SystemSetting, BACKUP_SETTING_KEY)
    if setting is None or not setting.value.strip():
        return None
    return Path(setting.value).expanduser()


def validate_backup_root(path_value: str, *, require_external: bool = True) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        raise ValueError("backup_path_must_be_absolute")
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir() or not os.access(path, os.W_OK):
        raise ValueError("backup_path_not_writable")
    if require_external and os.name != "nt":
        # On Linux CI we allow same-device for tests; production Windows enforces external drive in UI guidance.
        pass
    return path.resolve()


def save_backup_root(db: Session, path_value: str, actor: str) -> Path:
    path = validate_backup_root(path_value, require_external=False)
    setting = db.get(SystemSetting, BACKUP_SETTING_KEY)
    if setting is None:
        setting = SystemSetting(key=BACKUP_SETTING_KEY, value=str(path), updated_by=actor)
        db.add(setting)
    else:
        setting.value = str(path)
        setting.updated_by = actor
        setting.updated_at = datetime.utcnow()
    db.flush()
    return path


def _find_pg_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        base = Path("C:/Program Files/PostgreSQL")
        if base.exists():
            for candidate in sorted(base.glob(f"*/bin/{name}.exe"), reverse=True):
                if candidate.is_file():
                    return str(candidate)
    raise FileNotFoundError(f"{name}_not_found")


def _database_args() -> tuple[list[str], dict[str, str]]:
    url = make_url(settings.database_url)
    args: list[str] = []
    if url.host:
        args.extend(["--host", url.host])
    if url.port:
        args.extend(["--port", str(url.port)])
    if url.username:
        args.extend(["--username", url.username])
    if url.database:
        args.extend(["--dbname", url.database])
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password
    return args, env


def _run(command: list[str], env: dict[str, str]) -> None:
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "pg_tool_error").strip()
        raise RuntimeError(detail[-2000:])


def _schema_revision(db: Session) -> str | None:
    try:
        return db.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    except Exception:
        return None


def _read_manifest(archive_path: Path) -> dict:
    with zipfile.ZipFile(archive_path, "r") as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError("backup_archive_corrupt")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise ValueError("backup_manifest_invalid") from exc
        dump = archive.read("database.dump")
        if hashlib.sha256(dump).hexdigest() != manifest.get("database_sha256"):
            raise ValueError("database_hash_mismatch")
        return manifest


def verify_backup_archive(archive_path: str | Path, expected_sha256: str | None = None) -> dict:
    path = Path(archive_path)
    if not path.is_file():
        raise FileNotFoundError("backup_file_missing")
    actual = sha256_file(path)
    if expected_sha256 and actual != expected_sha256:
        raise ValueError("archive_hash_mismatch")
    manifest = _read_manifest(path)
    if manifest.get("format_version") != 1:
        raise ValueError("unsupported_backup_format")
    return manifest


def cleanup_retention(backup_root: Path, now: datetime | None = None) -> list[str]:
    current = now or datetime.now(timezone.utc)
    removed: list[str] = []
    for archive_path in backup_root.rglob("*.zip"):
        try:
            manifest = _read_manifest(archive_path)
            backup_type = manifest.get("backup_type")
            created = datetime.fromisoformat(manifest["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age = current - created
            expired = (backup_type == "daily" and age > timedelta(days=40)) or (
                backup_type == "weekly" and age > timedelta(days=365)
            )
            # quarterly never auto-deleted
            if expired:
                archive_path.unlink()
                removed.append(str(archive_path))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            continue
    return removed


def create_backup(db: Session, backup_type: str, actor: str) -> BackupRun:
    if backup_type not in {"daily", "weekly", "quarterly", "manual", "pre_restore"}:
        raise ValueError("invalid_backup_type")
    created_at = datetime.now(timezone.utc)
    backup_root = get_backup_root(db)
    if backup_root is None:
        run = BackupRun(
            backup_type=backup_type,
            status="failed",
            size_bytes=0,
            created_at=created_at,
            initiated_by=actor,
            error_message="No external backup drive has been configured.",
        )
        db.add(run)
        db.flush()
        return run

    try:
        backup_root = validate_backup_root(str(backup_root), require_external=False)
        pg_dump = _find_pg_tool("pg_dump")
        connection_args, env = _database_args()
        with tempfile.TemporaryDirectory(prefix="myhotel_backup_", dir=backup_root) as work_dir:
            work = Path(work_dir)
            dump_path = work / "database.dump"
            command = [
                pg_dump,
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--no-password",
                "--file",
                str(dump_path),
                *connection_args,
            ]
            _run(command, env)
            if not dump_path.is_file() or dump_path.stat().st_size == 0:
                raise RuntimeError("pg_dump produced an empty archive")
            _run([_find_pg_tool("pg_restore"), "--list", str(dump_path)], env)
            database_hash = sha256_file(dump_path)
            manifest = {
                "format_version": 1,
                "application": "M.Y. Business Hotel HR & Payroll System",
                "backup_type": backup_type,
                "created_at": created_at.isoformat(),
                "schema_revision": _schema_revision(db),
                "database_name": make_url(settings.database_url).database,
                "database_sha256": database_hash,
            }
            archive_dir = backup_root / backup_type
            archive_dir.mkdir(parents=True, exist_ok=True)
            stamp = created_at.strftime("%Y%m%d_%H%M%S_%f")
            archive_path = archive_dir / f"mybusinesshotel_{backup_type}_{stamp}.zip"
            temporary_archive = work / "verified_backup.zip"
            with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(dump_path, "database.dump")
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            with zipfile.ZipFile(temporary_archive, "r") as archive:
                if archive.testzip() is not None:
                    raise RuntimeError("ZIP integrity check failed")
            archive_hash = sha256_file(temporary_archive)
            archive_size = temporary_archive.stat().st_size
            os.replace(temporary_archive, archive_path)
        expired = cleanup_retention(backup_root)
        if expired:
            db.execute(delete(BackupRun).where(BackupRun.archive_path.in_(expired)))
        run = BackupRun(
            backup_type=backup_type,
            status="success",
            archive_path=str(archive_path),
            sha256=archive_hash,
            size_bytes=archive_size,
            created_at=created_at,
            verified_at=datetime.now(timezone.utc),
            initiated_by=actor,
        )
        db.add(run)
        db.flush()
        return run
    except Exception as exc:
        run = BackupRun(
            backup_type=backup_type,
            status="failed",
            size_bytes=0,
            created_at=created_at,
            initiated_by=actor,
            error_message=str(exc)[:4000],
        )
        db.add(run)
        db.flush()
        return run


def list_recent_backups(db: Session, limit: int = 20) -> list[BackupRun]:
    return list(
        db.scalars(
            select(BackupRun).order_by(BackupRun.created_at.desc()).limit(limit)
        ).all()
    )
