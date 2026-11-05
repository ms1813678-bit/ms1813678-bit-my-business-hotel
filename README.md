# M.Y. Business Hotel — HR & Payroll System

A local-first, Arabic/English HR and payroll application for M.Y. Business Hotel in Maadi, Cairo.

## Current status

**Application foundation in progress — not production-ready.** The repository contains a FastAPI shell, PostgreSQL configuration, initial HR models, Argon2 password hashing, Arabic RTL pages, database-backed administrator authentication, session-bound CSRF protection, and integration tests.

## Requirements

See [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md) for the agreed product scope and business rules.

## Local development (Windows)

1. Install Python 3.11+ and PostgreSQL.
2. Create a local PostgreSQL database and user with a strong, unique password.
3. Copy `.env.example` to `.env`.
4. Set `DATABASE_URL` to your real PostgreSQL URL, e.g. `postgresql+psycopg://hr_app:YOUR_PASSWORD@127.0.0.1:5432/my_business_hotel_hr`. The application refuses missing values and example placeholders such as `CHANGE_ME`.
5. Generate a unique session key in PowerShell: `python -c "import secrets; print(secrets.token_urlsafe(48))"`, then set `SESSION_SECRET` in `.env`.
6. Install: `python -m pip install -e ".[dev]"`.
7. Apply schema migrations: `python -m scripts.initialize_database`.
8. Create the only administrator from the local terminal: `python -m scripts.create_admin`. The password is entered invisibly and is never configured through a public signup page.
10. Run: `uvicorn app.main:app --host 127.0.0.1 --port 8000`.
11. Open `http://127.0.0.1:8000`. The `/health` endpoint returns HTTP 503 if PostgreSQL cannot be reached.

The session cookie uses an eight-hour lifetime, HttpOnly, and SameSite=Lax. Secure cookies are enabled automatically when `APP_ENV=production`; local HTTP development keeps Secure disabled. Keep the server bound to `127.0.0.1`; never expose it to the public internet.

## Safety

- This is not production-ready. Do not enter real employee data until migrations, authorization, backup/restore, Windows installation, and security tests are complete.
- Do not use production payroll data until calculations and approval controls are implemented and verified.
- The administrator account is intentionally non-deletable and cannot be deactivated; use a controlled password-change procedure when that feature is implemented.
