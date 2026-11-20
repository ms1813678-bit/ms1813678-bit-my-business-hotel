from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_setup_assets_exist():
    assert (ROOT / "scripts" / "windows" / "setup.ps1").is_file()
    assert (ROOT / "scripts" / "windows" / "start.ps1").is_file()
    assert (ROOT / "scripts" / "generate_monthly_report.py").is_file()
    assert (ROOT / "docs" / "WINDOWS_INSTALL.md").is_file()


def test_windows_setup_preserves_existing_env_and_uses_secure_database_password_prompt():
    setup = (ROOT / "scripts" / "windows" / "setup.ps1").read_text(encoding="utf-8")
    assert 'if (-not (Test-Path ".env"))' in setup
    assert 'Read-Host "Database password" -AsSecureString' in setup
    assert "secrets.token_urlsafe(48)" in setup
    assert "scripts.initialize_database" in setup
    assert "scripts.seed_departments" in setup
    assert "scripts.seed_shifts" in setup
    assert "Database port must be a number between 1 and 65535." in setup
    assert "scripts.create_admin" in setup


def test_windows_launcher_is_local_and_manual():
    launcher = (ROOT / "scripts" / "windows" / "start.ps1").read_text(encoding="utf-8")
    assert "--host 127.0.0.1" in launcher
    assert "--port 8000" in launcher
    assert "uvicorn app.main:app" in launcher
    assert "Start-Process" in launcher
    assert "schtasks" not in launcher.lower()




def test_windows_setup_has_balanced_powershell_blocks():
    setup = (ROOT / "scripts" / "windows" / "setup.ps1").read_text(encoding="utf-8")
    # Catch the most common accidental corruption: an unfinished conditional or here-string.
    assert "if (-not [int]::TryParse($DbPortInput, [ref]$ParsedPort)" in setup
    assert '$EnvContent = @"' in setup
    assert setup.count('$EnvContent = @"') == 1
    assert setup.count('"@') == 1


def test_monthly_reports_remain_manual_and_no_scheduled_report_task_is_installed():
    setup = (ROOT / "scripts" / "windows" / "setup.ps1").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "WINDOWS_INSTALL.md").read_text(encoding="utf-8")
    task_scripts = list((ROOT / "scripts" / "windows").glob("*report*task*.ps1"))

    assert not task_scripts
    assert "generated manually by the administrator" in setup
    assert "no automatic report email or scheduled report job" in docs.lower()
    assert "register_monthly_report_task.ps1" not in setup
