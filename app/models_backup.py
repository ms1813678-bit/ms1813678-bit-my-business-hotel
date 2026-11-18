"""Backup run and system setting persistence for external-drive recovery."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SystemSetting(Base):
    """Simple key/value settings store (backup root path, etc.)."""
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(80), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class BackupRun(Base):
    """Record of a backup or pre-restore attempt with verification metadata."""
    __tablename__ = "backup_runs"
    __table_args__ = (
        CheckConstraint(
            "backup_type IN ('daily', 'weekly', 'quarterly', 'manual', 'pre_restore')",
            name="ck_backup_run_type",
        ),
        CheckConstraint(
            "status IN ('success', 'failed')",
            name="ck_backup_run_status",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_backup_run_size_nonnegative"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    backup_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    archive_path: Mapped[str | None] = mapped_column(String(500))
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    initiated_by: Mapped[str] = mapped_column(String(80), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
