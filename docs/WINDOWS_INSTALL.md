# Windows 10 / Windows 11 installation and first administrator

This is the initial setup path for the local M.Y. Business Hotel HR application. It does not install a Windows service or configure auto-start.

## Prerequisites
1. Windows 10 or Windows 11.
2. Python 3.11 or newer with the Python Launcher for Windows.
3. PostgreSQL 16 (or a compatible supported release), installed and running locally.
4. A dedicated PostgreSQL database and database user with schema permissions.
5. The project repository checked out locally.

Do not use a production database until the complete application, security, backup, and restore tests are approved.

## First installation
1. Open the project folder.
2. Open PowerShell in the project folder.
3. Run:
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .\scripts\windows\setup.ps1
4. Setup creates a project-local virtual environment and installs dependencies.
5. Enter PostgreSQL connection details; the password is requested as hidden input.
6. Setup writes .env only when it does not already exist.
7. Setup applies migrations, seeds missing standard departments and configured shift patterns, and creates the first administrator.
8. A desktop shortcut named M.Y. Business Hotel HR is created after successful setup.

## Reports
Reports are generated manually by the administrator and saved locally as Excel or PDF. There is no automatic report email or scheduled report job in the initial release.

## Daily start and stop
- Double-click the M.Y. Business Hotel HR desktop shortcut.
- The browser opens at http://127.0.0.1:8000.
- Keep the PowerShell window open while using the application.
- Press Ctrl+C to stop the application.
- PostgreSQL must be running before starting the application.

The application binds only to 127.0.0.1. Do not expose it publicly.

## Backup and restore acceptance
Before production use, verify backup creation, SHA-256/integrity validation, retention behavior, restore confirmation and audit, automatic pre-restore backup, successful restore verification, and rollback after an intentionally failed restore in a disposable test database.

## Troubleshooting
- .env contains database credentials and the session secret. Never commit or share it.
- If database connection fails, verify PostgreSQL is running and the database credentials are correct.
- If port 8000 is in use, stop the other local process.
- /health reports database connectivity only; it does not certify release readiness.

## Release limitation
This branch is feature-complete for local testing, but it is not production-accepted until the final acceptance gates in docs/PROJECT_HANDOFF.md are executed. Use synthetic records only until then.
