# M.Y. Business Hotel — HR & Payroll System

A local-first, Arabic/English HR and payroll application for M.Y. Business Hotel in Maadi, Cairo.

## Current status

**Application foundation in progress — not production-ready.** The repository contains:
- FastAPI application shell and health endpoint.
- PostgreSQL connection configuration through environment settings.
- Initial SQLAlchemy models for departments, branches, employees, employee change history, audit logs, and the single administrator account.
- Argon2-capable password hashing utility with a minimum password length.
- Arabic RTL dashboard shell and responsive styling.
- Authentication, database-backed administrator session validation, session-bound CSRF protection, and integration tests for protected routes.

## Requirements

See [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md) for the agreed product scope and business rules.

## Local development (Windows)

1. Install Python 3.11+ and PostgreSQL.
2. Create a local database and database user with a strong password.
3. Copy `.env.example` to `.env` and set `DATABASE_URL`.
4. Generate a unique session signing key in PowerShell:
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`
5. Put the generated value in `.env` as `SESSION_SECRET=...`. Do not commit `.env` or share this value. The application refuses to start if the secret is missing, too short, or the example placeholder.
6. Install: `python -m pip install -e ".[dev]"`.
7. Run: `uvicorn app.main:app --host 127.0.0.1 --port 8000`.
8. Open `http://127.0.0.1:8000`. Health check: `http://127.0.0.1:8000/health`.

The session cookie uses an eight-hour lifetime, HttpOnly, and SameSite=Lax. Secure cookies are enabled automatically when `APP_ENV=production`; local development over HTTP keeps Secure disabled so login works on Windows localhost. Keep the server bound to `127.0.0.1`; never expose it to the public internet.

## Safety

- Do not enter real employee data until authentication, migrations, authorization, backup/restore, and security tests are complete.
- Do not use production payroll data until calculations and approval controls are implemented and verified.
