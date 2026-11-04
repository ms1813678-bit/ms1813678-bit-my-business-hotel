# M.Y. Business Hotel — HR & Payroll System

A local-first, Arabic/English HR and payroll application for M.Y. Business Hotel in Maadi, Cairo.

## Current status

**Initial application scaffold — not production-ready.** The repository contains:
- FastAPI application shell and health endpoint.
- PostgreSQL connection configuration through environment settings.
- Initial SQLAlchemy models for departments, branches, employees, employee change history, audit logs, and the administrator account.
- Argon2-capable password hashing utility with a minimum password length.
- Arabic RTL dashboard shell and responsive styling.
- Starter security unit tests.

Still required: database migrations and initialization, complete authentication/session flow, employee workflows, attendance import/reconciliation/approval, leave, advances, sanctions/rewards, payroll calculations and approval, reports, backup/restore, Windows packaging, and full testing.

## Requirements

See [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md) for the agreed product scope and business rules.

## Local development

1. Install Python 3.11+ and PostgreSQL.
2. Create a local database and database user with a strong password.
3. Copy `.env.example` to `.env`; set `DATABASE_URL` and a long random `SESSION_SECRET`.
4. Install: `python -m pip install -e ".[dev]"`.
5. Run: `uvicorn app.main:app --host 127.0.0.1 --port 8000`.
6. Open `http://127.0.0.1:8000`. Health check: `http://127.0.0.1:8000/health`.

The database schema is not yet migrated or initialized. The dashboard is a visual scaffold and does not yet provide authenticated HR operations.

## Safety

- Keep the server bound to `127.0.0.1`; never expose it to the public internet.
- Do not enter real employee data until authentication, migrations, authorization, backup/restore, and security tests are complete.
- The starter tests have not been executed in this environment.
- Do not use production payroll data until calculations and approval controls are implemented and verified.
