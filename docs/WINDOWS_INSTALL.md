# Windows 11 installation and first administrator

This is the initial setup path for the local M.Y. Business Hotel HR application. It does not install a Windows service or configure auto-start.

## Prerequisites

1. Windows 11.
2. Python 3.11 or newer, installed with the Python Launcher for Windows (py).
3. PostgreSQL 16 (or a compatible supported release), installed and running locally.
4. A dedicated PostgreSQL database and database user created in pgAdmin or PostgreSQL tools, with schema permissions for that database.
5. The project repository checked out locally.

Do not use a production database until the complete application, security, backup, and restore tests are approved.

## First installation

1. Open the project folder in File Explorer.
2. Open Terminal (PowerShell) in the project folder.
3. Run the following commands:

   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .\scripts\windows\setup.ps1

4. Setup creates a project-local virtual environment and installs dependencies.
5. Enter PostgreSQL host, port, database name, username, and password. The database password is requested as hidden secure input. A unique session secret is generated locally.
6. The setup writes .env only when it does not already exist. Existing configuration is preserved.
7. Setup applies database migrations, seeds missing standard departments, and prompts for the first administrator username and password. Password input is hidden and the password must be at least 12 characters.
8. A desktop shortcut named M.Y. Business Hotel HR is created after successful setup.

## Reports

Reports are generated manually by the administrator. Select the report type, start date, end date, and either all employees or one employee, then generate Excel or PDF. Report files are saved locally. Setup does not register a scheduled report task, and the application does not email reports.

## First administrator

The first administrator is created only once. A second administrator is rejected by application logic and database constraints. There is no public signup page. If an administrator already exists, setup does not replace its credentials.

## Daily start and stop

- Double-click the M.Y. Business Hotel HR desktop shortcut.
- The browser opens at http://127.0.0.1:8000.
- Keep the PowerShell window open while using the application.
- Press Ctrl+C in that window to stop the application.
- PostgreSQL must be running before starting the application.

The application binds only to 127.0.0.1. Do not change this to 0.0.0.0, configure router port forwarding, or expose the service publicly. No Windows auto-start task, application service, or scheduled report task is created.

## Configuration and troubleshooting

- .env contains database credentials and the session secret. Never commit, send, or screenshot it.
- If database connection fails, confirm PostgreSQL is running and verify database, user, password, host, port, and permissions. Correct .env and rerun setup.
- If port 8000 is in use, stop the other local process before starting the application.
- Keep the project folder in a stable location; the desktop shortcut points to it.
- Do not delete .venv or .env while the application is in use.
- /health reports database connectivity; it does not certify that all product modules are complete.

## Current delivery limitation

This installs and launches the current application foundation. It is not yet a final end-user installer package. Full HR/payroll modules and backup/restore remain separate delivery work. Do not enter real employee or payroll data until all project delivery gates are complete.
