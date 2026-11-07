# Team 1 Foundation and Integration Audit

Reviewed: 2026-11-07
Working branch: `work/team1-foundation-integration`
Audit base: `feature/platform-hardening`
Scope: repository architecture, employee foundation, authentication/security, migration graph, and cross-workstream integration safety.

## 1. Verified baseline

The audit branch was created from `feature/platform-hardening`; no existing branch or commit was rewritten.

The inspected baseline contains:
- FastAPI application with local PostgreSQL configuration and a single administrator account.
- Administrator authentication, password hashing, session-backed authentication, CSRF helpers, and session-version invalidation.
- Employee, department, branch, employee-change, employee-data-change-request, employee-code-counter, and employee-service-period models.
- Employee code allocation using a PostgreSQL non-transactional sequence; SQLite allocation is explicitly limited to isolated tests.
- Employee routes and pages for core employee workflows, including archive/rehire and data-change handling.
- Audit log immutability protections.
- Attendance modules and migrations through revision `0015_attendance_issue_resolution`.

Presence of code is not equivalent to full acceptance. The actual Windows 11 target-device installation and end-to-end database acceptance have not been performed in this audit.

## 2. Migration graph inventory

Inspected revision chain:

```text
0001_initial
  -> 0002_platform_state
  -> 0003_employee_service_periods
       -> 0004_employee_code_sequence --------       -> 0004_non_reusable_employee_codes ---+-> 0005_merge_code_heads
                                                -> 0006_employee_probation_dates
                                                -> 0007_attendance_shift_foundation
                                                -> 0008_shift_matching_margins
                                                -> 0009_audit_log_immutability
                                                -> 0010_attendance_records
                                                -> 0011_attendance_import_issues
                                                -> 0012_attendance_approval_values
                                                -> 0013_attendance_months
                                                -> 0014_attendance_source_files
                                                -> 0015_attendance_issue_resolution
```

The two 0004 revisions are intentional parallel histories and are joined by 0005. Do not delete or rename either historical revision. The 0003 and later employee-history downgrade behavior intentionally retains service-period history. The 0004 code-sequence revisions also preserve the non-transactional sequence/high-water mark on downgrade.

Acceptance still requires running the PostgreSQL migration suite against a clean database and a database with historical data, confirming one Alembic head, and testing upgrade/downgrade/re-upgrade without losing employee-code reservations or service-period history. The source files alone do not prove these runtime gates.

## 3. Employee foundation and invariants observed

- Employee codes use the `MY-0001` format and a database-backed sequence for PostgreSQL.
- Employee code reservations are designed not to be reused after a transaction rollback.
- Service-period history is separate from the employee's current status.
- The service-period migration refuses to invent an unknown rehire/start date and refuses to proceed when conflicting open/closed history is detected.
- A partial unique index is intended to allow only one open service period per employee.
- Archive/rehire history and employee-code retention have dedicated acceptance tests/PRs.

Remaining coordinator verification:
1. Confirm employee creation, editing, archive, rehire, and former-employee listing as one complete route-to-database workflow.
2. Confirm rehire retains the original employee code and creates exactly one new open service period.
3. Confirm failed/rolled-back creation does not permit a PostgreSQL employee code to be reused.
4. Confirm migration downgrade/re-upgrade retains service periods and sequence high-water marks.
5. Confirm all employee mutations are authenticated, CSRF-protected, and auditable.
6. Confirm photo uploads validate actual image content, size, and supported format, and that file cleanup/backup behavior is covered.

## 4. Security review status

The inspected platform-hardening baseline includes:
- Argon2-capable password hashing through the configured password-hash library, with a minimum 12-character password.
- A local terminal bootstrap script that creates one administrator and refuses a second administrator.
- Session username resolution against an active administrator record.
- Session-version checking; stale or malformed session versions fail closed and clear the session.
- CSRF tokens bound to the session and checked with constant-time comparison.
- Login rotates the session by clearing it before writing the authenticated identity and issuing a fresh CSRF token.
- Database constraints/tests intended to prevent multiple administrator rows and prevent deletion/deactivation of the sole administrator.

Open security acceptance:
- Re-run the full authentication, route-security, CSRF, administrator-bootstrap, and audit immutability tests on the current integration head.
- Verify cookie settings, local-only bind, secret configuration, session expiry, and production-mode HTTPS behavior against the actual Windows setup.
- Verify every state-changing route has both administrator authentication and CSRF protection; review upload/download endpoints separately.
- Verify audit-log immutability at both application and database levels.
- Do not claim production security approval until these checks pass.

## 5. Pull request collision finding — PR #1

PR #1 (`feature/auth-security`) is not a narrow security-only change. Its current changed-file list includes:
- Shared files: `app/main.py`, `app/models.py`, configuration and templates.
- Attendance-owned implementation: `app/attendance_workflow.py` and attendance workflow tests.
- Migration `0014_attendance_source_files.py`.
- Authentication, CSRF, dependency, and security files.

PR #1 targets `main`, while the attendance acceptance PR #2 targets `feature/platform-hardening` and already contains the attendance source-file migration/work. This creates overlapping ownership and potential duplicate migration/revision history. PR #1's latest reported workflow jobs failed before test steps started, so its code is not CI-verified.

**Decision:** PR #1 must remain unmerged pending a controlled split/rebase/review. Its authentication-only changes should be isolated from attendance-owned files and reconciled against the current integration baseline. Do not merge PR #1 directly into `main`, and do not merge it before resolving the duplicate attendance/migration content.

## 6. Integration boundaries

- Team 1 is the only editor of shared models, application entrypoint, Alembic migrations, common settings, and shared navigation.
- Teams 2–4 must submit schema changes as proposals; they must not independently edit shared migrations or models.
- Attendance, leave/advances/rewards, and payroll/reporting remain owned by their respective workstreams.
- No branch is accepted solely because its unit tests pass; review changed paths, migration compatibility, CI, and end-to-end workflows first.
- No workstream may merge directly to `main`.

## 7. Team 1 next actions

1. Complete a full review of PR #1's authentication changes and isolate the valid security-only delta.
2. Run and review employee lifecycle and admin-security test suites on a common integration head.
3. Verify the full Alembic graph and PostgreSQL upgrade/downgrade/re-upgrade acceptance.
4. Review all incoming PRs for shared-file/migration collisions before integration.
5. Integrate only reviewed work in dependency order, then run full cross-module regression.
6. Coordinate Windows 11 installation, backup/restore, and final user acceptance; these cannot be claimed complete from GitHub CI alone.

## 8. Current status

This commit records an evidence-based audit and integration blockers only. It does not claim that the full application is complete, that PR #1 is approved, or that the target Windows device has passed acceptance.
