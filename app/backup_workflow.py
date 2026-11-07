"""Verified local PostgreSQL backups and retention management."""
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

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import settings
from app.models import BackupRun, SystemSetting


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PHOTO_DIRECTORY = PROJECT_ROOT / "var" / "employee_photos"
BACKUP_SETTING_KEY = "backup_root"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_directory(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    if path.exists():
        for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
            relative = file_path.relative_to(path).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(sha256_file(file_path).encode("ascii"))
            count += 1
    return digest.hexdigest(), count


def get_backup_root(db: Session) -> Path | None:
    setting = db.get(SystemSetting, BACKUP_SETTING_KEY)
    if setting is None or not setting.value.strip():
        return None
    return Path(setting.value).expanduser()


def validate_backup_root(path_value: str, *, require_external: bool = True) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        raise ValueError("Backup location must be an absolute path on the external drive.")
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir() or not os.access(path, os.W_OK):
        raise ValueError("The backup location is not writable.")

    if require_external:
        if os.name == "nt":
            app_drive = Path(PROJECT_ROOT).resolve().drive.casefold()
            backup_drive = path.resolve().drive.casefold()
            if not backup_drive or backup_drive == app_drive:
                raise ValueError("Choose a different drive from the Windows system/application drive.")
        else:
            app_device = os.stat(PROJECT_ROOT).st_dev
            backup_device = os.stat(path).st_dev
            if app_device == backup_device:
                raise ValueError("The backup folder must be on a separate mounted drive.")
    return path.resolve()


def save_backup_root(db: Session, path_value: str, actor: str) -> Path:
    path = validate_backup_root(path_value, require_external=True)
    setting = db.get(SystemSetting, BACKUP_SETTING_KEY)
    if setting is None:
        setting = SystemSetting(
            key=BACKUP_SETTING_KEY,
            value=str(path),
            updated_by=actor,
        )
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
        for candidate in sorted(Path("C:/Program Files/PostgreSQL").glob(f"*/bin/{name}.exe"), reverse=True):
            if candidate.is_file():
                return str(candidate)
    raise FileNotFoundError(f"{name} was not found. Install PostgreSQL client tools and add them to PATH.")


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


def _run(command: list[str], env: dict[str, str], *, cwd: Path = PROJECT_ROOT) -> None:
    result = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Unknown PostgreSQL tool error").strip()
        raise RuntimeError(detail[-4000:])


def _schema_revision(db: Session) -> str | None:
    try:
        return db.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    except Exception:
        return None


def _hash_zip_photos(archive: zipfile.ZipFile) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for name in sorted(
        item for item in archive.namelist()
        if item.startswith("employee_photos/") and not item.endswith("/")
    ):
        relative = name.split("/", 1)[1]
        digest.update(relative.encode("utf-8"))
        digest.update(hashlib.sha256(archive.read(name)).hexdigest().encode("ascii"))
        count += 1
    return digest.hexdigest(), count


def _read_manifest(archive_path: Path) -> dict:
    with zipfile.ZipFile(archive_path, "r") as archive:
        bad_file = archive.testzip()
        if bad_file:
            raise ValueError(f"Backup archive integrity check failed at {bad_file}.")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise ValueError("Backup manifest is missing or invalid.") from exc
        dump = archive.read("database.dump")
        if hashlib.sha256(dump).hexdigest() != manifest.get("database_sha256"):
            raise ValueError("Database dump hash does not match the manifest.")
        photo_hash, photo_count = _hash_zip_photos(archive)
        if photo_hash != manifest.get("employee_photos_sha256"):
            raise ValueError("Employee photo archive hash does not match the manifest.")
        if photo_count != manifest.get("employee_photo_count"):
            raise ValueError("Employee photo count does not match the manifest.")
        return manifest


def verify_backup_archive(archive_path: str | Path, expected_sha256: str | None = None) -> dict:
    path = Path(archive_path)
    if not path.is_file():
        raise FileNotFoundError("The selected backup file does not exist.")
    actual_hash = sha256_file(path)
    if expected_sha256 and actual_hash != expected_sha256:
        raise ValueError("The backup archive hash does not match its saved record.")
    manifest = _read_manifest(path)
    if manifest.get("format_version") != 1:
        raise ValueError("Unsupported backup format version.")
    return manifest


def cleanup_retention(backup_root: Path, now: datetime | None = None) -> list[str]:
    current = now or datetime.now(timezone.utc)
    removed = []
    for archive_path in backup_root.rglob("*.zip"):
        try:
            manifest = _read_manifest(archive_path)
            backup_type = manifest.get("backup_type")
            created = datetime.fromisoformat(manifest["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age = current - created
            expired = (
                backup_type == "daily" and age > timedelta(days=40)
            ) or (
                backup_type == "weekly" and age > timedelta(days=365)
            )
            if expired:
                archive_path.unlink()
                removed.append(str(archive_path))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            # Never delete an unreadable archive automatically.
            continue
    return removed


def create_backup(db: Session, backup_type: str, actor: str) -> BackupRun:
    if backup_type not in {"daily", "weekly", "quarterly", "manual", "pre_restore"}:
        raise ValueError("Invalid backup type.")
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
        backup_root = validate_backup_root(str(backup_root), require_external=True)
        pg_dump = _find_pg_tool("pg_dump")
        pg_restore = _find_pg_tool("pg_restore")
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
                raise RuntimeError("pg_dump did not create a non-empty database archive.")

            _run([pg_restore, "--list", str(dump_path)], env)
            database_hash = sha256_file(dump_path)
            photos_hash, photo_count = _hash_directory(PHOTO_DIRECTORY)
            manifest = {
                "format_version": 1,
                "application": "M.Y. Business Hotel HR & Payroll System",
                "backup_type": backup_type,
                "created_at": created_at.isoformat(),
                "schema_revision": _schema_revision(db),
                "database_name": make_url(settings.database_url).database,
                "database_sha256": database_hash,
                "employee_photos_sha256": photos_hash,
                "employee_photo_count": photo_count,
            }
            archive_dir = backup_root / backup_type
            archive_dir.mkdir(parents=True, exist_ok=True)
            stamp = created_at.strftime("%Y%m%d_%H%M%S_%f")
            archive_path = archive_dir / f"mybusinesshotel_{backup_type}_{stamp}.zip"
            temporary_archive = work / "verified_backup.zip"
            with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(dump_path, "database.dump")
                if PHOTO_DIRECTORY.exists():
                    for file_path in sorted(item for item in PHOTO_DIRECTORY.rglob("*") if item.is_file()):
                        archive.write(
                            file_path,
                            f"employee_photos/{file_path.relative_to(PHOTO_DIRECTORY).as_posix()}",
                        )
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            with zipfile.ZipFile(temporary_archive, "r") as archive:
                if archive.testzip() is not None:
                    raise RuntimeError("The completed ZIP archive failed its integrity check.")
                verified_manifest = json.loads(archive.read("manifest.json"))
                if verified_manifest.get("database_sha256") != database_hash:
                    raise RuntimeError("The database dump hash changed during archive creation.")
            archive_hash = sha256_file(temporary_archive)
            archive_size = temporary_archive.stat().st_size
            os.replace(temporary_archive, archive_path)
        cleanup_retention(backup_root)
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
