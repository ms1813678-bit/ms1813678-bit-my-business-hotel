# M.Y. Business Hotel — HR & Payroll System

A local-first HR and payroll application for M.Y. Business Hotel, Maadi, Cairo. The target is a Windows 11 computer running a local FastAPI application and PostgreSQL database. The only application user is the HR Manager / System Administrator.

## Delivery status

**Still under development — not production-ready.** The feature branches contain substantial HR modules, but the complete release has not yet been integrated, installed, and accepted on the target Windows computer. Do not enter real employee or payroll data until the final delivery gates are passed.

## Implemented in the current feature stack

- Single administrator authentication, session security, CSRF protection, and immutable audit history.
- Employee register, department/branch assignment, employee code sequence, archive/rehire history, and employee change history.
- Attendance workbook imports, raw source retention, reconciliation, exception review, and explicit daily/monthly approval.
- Leave types, manually entered opening balances, leave confirmation/cancellation, and balance transaction history.
- Salary advances with the 15th–22nd request window, separate regular/installment rules, a 25% cap only for regular advances, and an auditable installment ledger consumed by payroll.
- Sanctions and rewards with manual financial amounts and payroll-month assignment.
- Monthly payroll runs, employee snapshots, approval/close/reopen workflow, manual insurance/tax entries, and cash/bank payment ledger.
- Multi-sheet Excel reports, Arabic-capable PDF reports, and live HR dashboard metrics. Reports are requested manually by the administrator with an explicit date range and either all employees or one selected employee.

These modules are still being reviewed and tested on separate pull requests. Their presence on feature branches does not mean they have been merged into main.

## Still required before release

- Final integration and migration review across all feature branches.
- Complete Arabic/English language selection and consistent RTL/LTR coverage.
- Verified backup and restore system, including external-drive retention and restore testing.
- Optional local/offline AI assistant remains to be integrated; it must never be required for core operations.
- Reports specification is manual: the administrator chooses report type, start/end dates, and all employees or one employee.
- Full Windows 11 installation, upgrade, backup/restore, and end-to-end acceptance on the target computer.
- Final review of unresolved payroll policy wording before approving live payroll calculations.

## Local Windows setup

See docs/WINDOWS_INSTALL.md for the current installation path. The application binds to 127.0.0.1 only and must not be exposed to the public internet.

## Business rules and scope

See docs/PROJECT_SPEC.md for the approved product scope and detailed HR rules. Purchasing, inventory, accounting, and other future modules are intentionally separate from this HR system.

## Safety

- Use synthetic test records only until release approval.
- Never commit .env, database passwords, session secrets, or real employee data.
- Payroll must not be approved before the exact month's attendance is reviewed and approved.
- Reports are read-only and generated manually by the administrator for a selected date range and employee scope; files are saved locally and are not emailed or scheduled automatically.
- main remains unchanged until the user explicitly approves integration.
