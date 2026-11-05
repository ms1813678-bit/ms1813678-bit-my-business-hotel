from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_setup_assets_exist():
    assert (ROOT / "scripts" / "windows" / "setup.ps1").is_file()
    assert (ROOT / "scripts" / "windows" / "start.ps1").is_file()
    assert (ROOT / "docs" / "WINDOWS_INSTALL.md").is_file()


def test_windows_setup_preserves_existing_env_and_uses_secure_database_password_prompt():
    setup = (ROOT / "scripts" / "windows" / "setup.ps1").read_text(encoding="utf-8")
    assert 'if (-not (Test-Path ".env"))' in setup
    assert 'Read-Host "Database password" -AsSecureString' in setup
    assert "secrets.token_urlsafe(48)" in setup
    assert "scripts.initialize_database" in setup
    assert "scripts.seed_departments" in setup
    assert "scripts.create_admin" in setup


def test_windows_launcher_is_local_and_manual():
    launcher = (ROOT / "scripts" / "windows" / "start.ps1").read_text(encoding="utf-8")
    assert "--host 127.0.0.1" in launcher
    assert "--port 8000" in launcher
    assert "uvicorn app.main:app" in launcher
    assert "Start-Process" in launcher
    assert "schtasks" not in launcher.lower()
