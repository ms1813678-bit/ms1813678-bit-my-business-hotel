"""Guards for restore workflow (reason, path, hash)."""
import pytest

from app.backup_service import restore_backup, verify_backup_archive


def test_restore_requires_reason(tmp_path):
    with pytest.raises(ValueError) as exc:
        restore_backup(tmp_path / "missing.zip", "abc", actor="admin", reason="  ")
    assert str(exc.value) == "restore_reason_required"


def test_verify_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        verify_backup_archive(tmp_path / "nope.zip")
