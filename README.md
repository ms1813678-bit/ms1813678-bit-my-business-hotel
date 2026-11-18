# M.Y. Business Hotel — HR & Payroll System

A local-first, Arabic/English HR and payroll application for M.Y. Business Hotel in Maadi, Cairo.

## Current status

**Early implementation; not ready for production data.** The current application provides:
- PostgreSQL configuration and versioned Alembic migrations.
- Single-administrator password login with CSRF protection and persistent session checks.
- One-time interactive administrator setup; no default account or password.
- Department seeding and employee registration with permanent employee codes and audit history.
- Attendance import, reconciliation, and approval services, plus payroll formula helpers.

Still required before production: complete employee lifecycle and history workflows, attendance upload/review UI, leave, advances, sanctions/rewards, payroll workflow and approval, reports, backup/restore verification, Windows packaging, and end-to-end security and recovery testing.

## Requirements

See [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md) for the agreed product scope and business rules.

## Local development

1. Install Python 3.11+ and PostgreSQL; create a local database and database user.
2. Copy `.env.example` to `.env`, then set `DATABASE_URL` and a private random `SESSION_SECRET` of at least 32 characters. Generate one with:
   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
3. Install: `python -m pip install -e ".[dev]"`.
4. Initialize or upgrade the database: `python scripts/initialize_database.py`.
5. Add the configurable default departments: `python scripts/seed_departments.py`.
6. Create the sole administrator interactively: `python scripts/create_admin.py`. The password is entered at the prompt and is never shown or stored in reversible form.
7. Run locally: `uvicorn app.main:app --host 127.0.0.1 --port 8000`.
8. Open `http://127.0.0.1:8000`; health check: `http://127.0.0.1:8000/health`.

Run the application from any directory after installation; templates and static assets resolve from the project path. The server must remain bound to the local computer and must not be exposed to the internet.

## Safety

- Back up the database before the first migration on an existing database.
- Use only test data until all required modules, authorization, backup/restore, and security checks are complete.
- Do not use production payroll data until calculations and approval controls are implemented and verified.
