# M.Y. Business Hotel — HR & Payroll System

A local-first, Arabic/English human-resources and payroll application for M.Y. Business Hotel in Maadi, Cairo.

## Project status

**Planning/specification stage.** The repository initially contained only this README. The requirements have now been documented; application code, database migrations, automated tests, and Windows packaging have not yet been implemented.

## Requirements

See [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md) for the agreed product scope, HR workflows, attendance import rules, payroll calculations, security, and backup requirements.

## Delivery approach

1. Confirm the project structure and implementation stack.
2. Build the local application foundation and PostgreSQL schema.
3. Implement employee records and history.
4. Implement attendance imports, reconciliation, and approval.
5. Implement leave, advances, sanctions/rewards, and payroll.
6. Add reports, backup/restore, and optional local AI.
7. Test installation and recovery on the target Windows 11 laptop.

## Non-negotiable principles

- One administrator account only (HR Manager).
- Arabic-first interface with English support.
- Local/offline core operation; no mandatory paid hosting or AI API.
- Never invent attendance events, leave balances, or payroll inputs.
- Attendance approval is required before payroll finalization.
- No feature is described as complete until implemented and tested.
