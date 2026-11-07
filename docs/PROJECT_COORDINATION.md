# Project Coordination & Verified Work Plan
# M.Y. Business Hotel HR & Payroll System

Last reviewed: 2026-11-07
Coordination branch: `coordination/four-team-project-plan`
Baseline: `feature/attendance-end-to-end-acceptance` at `539272d1ce7c500c93c1ebec9aed19e2a293117f`

## 1. Purpose and authority

This document is the single coordination register for the four parallel AI workstreams. The four workstreams are:
1. Coordinator/implementer: repository architecture, employee foundation, shared contracts, integration, review, and final technical acceptance.
2. AI workstream 2: attendance and security roster.
3. AI workstream 3: leave, advances, sanctions, and rewards.
4. AI workstream 4: payroll, reports/dashboard, and optional local AI assistant.

The coordinator is also an active implementer, not only a project manager. The coordinator owns technical integration and merge decisions. No workstream may merge directly into `main`.

Important operational limitation: assigning work in this register does not itself launch independent AI agents. Each external AI session must receive its task, work in its assigned branch, and publish its result before it can be reviewed here.

## 2. Product constraints (must not be changed by workstreams)

- Local-first Windows 11 application; PostgreSQL local database; service binds to 127.0.0.1 only.
- One administrator account only: HR Manager / System Administrator.
- Arabic default, English available, RTL/LTR, Africa/Cairo timezone, EGP.
- Core HR functions work without AI or internet. AI is optional, local/free preferred, and must not block normal operation.
- Attendance is entered/imported by HR; no employee mobile login, GPS, or self-check-in.
- Inventory/store management is a separate future system, not part of this HR release.
- No real employee/payroll data in tests.
- Preserve employee code reservations and never reuse employee codes.
- Preserve service-period history on downgrade/re-upgrade; do not invent unknown employment dates.
- No automatic approval of attendance, leave, payroll, or consequential AI-proposed changes.
- Do not claim Windows target-laptop acceptance until actually tested on that laptop.
- Do not claim production readiness until security, migrations, backup, restore, and end-to-end acceptance are verified.

## 3. Verified baseline inventory

The following was observed in the repository tree and source files at the baseline commit. "Present" means code/schema/tests exist; it does not automatically mean the complete user-facing workflow has passed acceptance.

### Foundation and employee records — present, acceptance review still required
- FastAPI application, SQLAlchemy models, PostgreSQL configuration, Alembic migrations.
- Single-admin model and authentication/security modules.
- Session/CSRF protections and route-security tests.
- AuditLog model and migration `0009_audit_log_immutability`.
- Department and Branch models and routes.
- Employee model, code counter, service-period history, employee-change records and data-change requests.
- Employee routes observed: list/create/edit, photo, transfer, archive, rehire, data-change request/review.
- Employee code and service-period migrations are present, including multiple employee-code migration revisions. Migration graph and upgrade/downgrade behavior require a dedicated review before new schema work.

### Attendance — substantial implementation present; external acceptance gates remain
- Attendance import, reconciliation, workflow, shift matching, security roster import/workflow modules.
- Attendance pages for imports, issues, reconciliation, review, months, and security roster.
- Migrations 0010–0015 for attendance records, issues, approval values, months, source files, and issue resolution.
- Acceptance test covers two source workbooks, retention of original workbook bytes/raw punches, human resolution of conflicts, explicit day approval, monthly approval, and audit events.
- PR #2 reports CI results: Ubuntu/PostgreSQL 189 passed; Windows platform-independent suite 182 passed.
- Still required: real hotel three-person security roster workbook test and full target Windows 11 installation/database acceptance.

### Payroll — calculation helpers exist; full payroll product workflow is not established
- `app/payroll_math.py` includes hourly rate, grace-adjusted lateness, calendar-month absence deduction, and new-hire prorating.
- `tests/test_payroll_math.py` covers these helper calculations.
- No complete payroll lifecycle was established in this review: period creation, source aggregation, preview, approval, close/reopen, payment tracking, and final reports must be inspected/implemented.

### Other modules — not yet verified as complete
- Leave balances and leave workflow.
- Advances and outstanding-balance/limit controls.
- Sanctions and rewards.
- Complete reports and Excel/PDF exports.
- Complete dashboard metrics.
- Optional local AI assistant.
- Verified backup/restore and retention policy.
- Final end-user Windows installer/launcher acceptance and Arabic user guide.

## 4. Four workstreams and exclusive ownership

### Workstream 1 — Coordinator / active implementer
Owns:
- Baseline architecture and shared interface contracts.
- Employee foundation gap analysis and fixes.
- Authentication, authorization, audit, migration integrity review.
- Shared database integration and migration coordination.
- Application shell, shared navigation/layout, common settings.
- Cross-workstream integration, code review, CI review, merge decisions.
- Backup/restore integration and final Windows delivery coordination.

Must not independently rewrite the attendance, leave, advances, sanctions, payroll, or reporting modules owned by other workstreams. May make integration fixes only after coordinating with the owner and documenting the reason.

### Workstream 2 — Attendance and security roster
Owns:
- `app/attendance_*.py`, `app/shift_matching.py`, `app/security_roster*.py`.
- Attendance/security-roster templates and attendance-specific tests/docs.
- Import, raw evidence retention, reconciliation, exception review, daily/monthly approval, overnight handling, roster validation, and attendance acceptance tests.

Must not edit employee models, shared database migrations, payroll calculation/workflow, shared navigation, authentication, or common templates without prior coordinator agreement.

### Workstream 3 — Leave, advances, sanctions, rewards
Owns:
- New leave, advance, sanction, and reward domain services/routes/templates/tests.
- Domain-specific business validation, history, and audit-event integration.
- Module documentation and isolated acceptance tests.

Must not edit employee core models, attendance, payroll, shared migration files, authentication, or shared UI components without prior coordinator agreement. Submit required schema changes as migration proposals to Workstream 1; do not create or edit migrations independently.

### Workstream 4 — Payroll, reports/dashboard, optional AI
Owns:
- Payroll workflow/service and payroll-specific templates/tests.
- Reports/export services and report templates.
- Dashboard aggregation/widgets.
- Optional local AI adapter and confirmation/preview workflow.
- Payroll/report/AI documentation and isolated tests.

Must not edit attendance, employee core models, shared database migrations, authentication, or common navigation without prior coordinator agreement. Submit schema changes as migration proposals to Workstream 1.

## 5. File ownership and collision prevention

1. Every workstream uses its own branch. No shared-branch concurrent coding.
2. A workstream may modify only its owned paths. Any cross-boundary change requires written coordinator approval before editing.
3. Shared files are single-writer: `app/models.py`, `app/main.py`, `alembic/**`, shared configuration/dependencies, shared layout/navigation, and global CSS. Workstream 1 is the only editor for these unless it explicitly grants a temporary file lock.
4. Schema requests from workstreams 2–4 must be submitted as a migration proposal containing: required table/columns/constraints/indexes, rationale, relationships, data migration/backfill, rollback safety, and tests. Workstream 1 implements/reviews the migration.
5. Never force-push, rewrite another branch, delete another workstream's commits, or reset shared refs.
6. Never update `main` directly. All changes go through PRs into the agreed integration branch; coordinator reviews and merges.
7. Keep PRs small and single-purpose. Do not combine unrelated module changes.
8. Before starting, read this file and the current issue/PR/branch state. Before finishing, update the task register and report changed paths.
9. If a collision or requirement ambiguity is found, stop editing the affected shared file and report the conflict; do not guess.
10. Do not claim another AI is working unless its active session/branch/PR provides evidence.

## 6. Business-rule blockers requiring coordinator reconciliation

- Payroll hourly-rate formula in current specification is basic salary / (daily hours × 30). A later user instruction in project history referred to changing the daily basis to 40 days. These statements conflict. Do not silently choose or change payroll math; coordinator must reconcile the latest approved rule before final payroll acceptance.
- Shift behavior has evolved in later user instructions; current `PROJECT_SPEC.md` contains configured shift patterns and a Security roster. Do not add employee shift accounts or mobile attendance. Any removal/change of shift-table behavior requires explicit reconciliation against the latest user-approved instruction.
- Overtime is payable only when management requested/approved it.
- Attendance must be approved before payroll finalization.
- Advances: new advance only if total outstanding after granting remains within 25% of basic salary; full deduction in the following month's payroll.
- Salary payment date is the 5th of the following month.
- Manual bonuses and manual insurance/tax values; do not invent legal/tax formulas.
- Email report delivery requires configured credentials and a confirmed successful send; local report generation must work without email.

## 7. Task register

Statuses:
- NOT_STARTED
- READY
- IN_PROGRESS
- BLOCKED
- REVIEW
- VERIFIED
- DEFERRED_EXTERNAL

| ID | Workstream | Task | Initial status | Acceptance evidence |
|---|---|---|---|---|
| COORD-01 | 1 | Establish this coordination register and baseline | REVIEW | This file committed on coordination branch |
| COORD-02 | 1 | Inspect all branches, PRs, and migration graph | IN_PROGRESS | Branch/PR comparison and migration graph report |
| COORD-03 | 1 | Audit employee routes and full service-period lifecycle | READY | CRUD/archive/rehire tests and UI workflow evidence |
| COORD-04 | 1 | Review auth/session/CSRF/audit controls and PR #1 | READY | Security review + CI + no regression |
| ATT-01 | 2 | Audit current attendance implementation without rewriting it | READY | Route-to-service-to-model map and gap list |
| ATT-02 | 2 | Verify import/reconciliation/approval invariants | READY | Acceptance tests for missing, duplicate, unknown, conflict cases |
| ATT-03 | 2 | Verify Security roster rules and overnight handling | READY | Unit/integration tests for OFF, 00:00, two/three staff, invalid rows |
| ATT-04 | 2 | Real hotel roster acceptance | DEFERRED_EXTERNAL | HR runs actual workbook and confirms results |
| ATT-05 | 2 | Target Windows 11 attendance/database acceptance | DEFERRED_EXTERNAL | Successful test on target laptop |
| LEAVE-01 | 3 | Implement leave types, records, balances, history | NOT_STARTED | CRUD, balance-once, history, date-boundary tests |
| ADV-01 | 3 | Implement advances and 25% outstanding cap | NOT_STARTED | Boundary and next-month deduction tests |
| SR-01 | 3 | Implement sanctions/rewards and history | NOT_STARTED | Type, manual amount, audit, payroll-link tests |
| PAY-01 | 4 | Reconcile payroll divisor/business rules before final engine | BLOCKED | Written decision recorded by coordinator |
| PAY-02 | 4 | Implement full payroll period/preview/approval/close workflow | NOT_STARTED | End-to-end payroll lifecycle tests |
| PAY-03 | 4 | Implement payment tracking and advance/attendance deductions | NOT_STARTED | Cash/bank and partial/full/unpaid tests |
| REP-01 | 4 | Implement dashboard and operational reports | NOT_STARTED | Metric correctness tests |
| REP-02 | 4 | Implement Excel/PDF exports and local monthly report generation | NOT_STARTED | File content and date/time tests |
| AI-01 | 4 | Implement optional local AI adapter with preview/confirm | NOT_STARTED | Offline fallback, no auto-approval, permission tests |
| SYS-01 | 1 | Implement and verify backup/restore/retention | NOT_STARTED | Verified backup integrity and isolated restore tests |
| SYS-02 | 1 | Final Windows 11 setup/launcher acceptance | NOT_STARTED | Clean install and repeat launch on target laptop |
| SYS-03 | 1 | Final integrated security and regression suite | NOT_STARTED | Full CI + end-to-end acceptance |
| DOC-01 | 1 | Arabic user guide and handover package | NOT_STARTED | Tested instructions and final release checklist |

## 8. Delivery gates

A task is not VERIFIED merely because code exists or unit tests pass. It must have:
- Implementation in the correct workstream branch.
- Tests for success, failure, boundary, and permission cases as applicable.
- Passing relevant CI.
- Review of data integrity, audit behavior, and compatibility.
- Successful integration test against dependent modules.
- Updated documentation and task register.

A module is accepted only after its complete user workflow is tested. The whole product is accepted only after:
1. all module gates pass;
2. database migrations upgrade safely;
3. security and single-admin controls pass;
4. backup and restore are verified;
5. integrated payroll/attendance/leave/advance workflows pass;
6. Windows 11 target-laptop acceptance passes;
7. Arabic guide and handover package are complete.

## 9. Existing PR protection

- PR #1 remains open pending security review and integration decision.
- PR #2 remains draft/open; its base is `feature/platform-hardening`, not `main`.
- Do not merge either PR merely because earlier CI passed. Review current head, base, changed files, migration compatibility, and current CI first.
- Do not change `main` until the coordinator has verified the integration plan and the relevant PR is approved for merge.

## 10. Workstream handoff report format

Every workstream must submit:
- Task IDs completed and remaining.
- Branch name and head commit SHA.
- Exact changed file paths.
- Summary of behavior implemented.
- Tests run and exact pass/fail counts.
- Known limitations and blockers.
- Confirmation that no out-of-scope files were edited.
- Any requested shared-file/schema changes (as proposals only).

End of coordination register.
