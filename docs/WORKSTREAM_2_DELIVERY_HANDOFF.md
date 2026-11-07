# Workstream 2 delivery handoff — Attendance & Security roster

Date: 2026-11-07

## Scope and branch

- Repository: `ms1813678-bit/ms1813678-bit-my-business-hotel`
- Working branch: `work/attendance-review`
- Pull request: [#15 — Attendance: enforce fixed Security roster rules and harden Excel import](https://github.com/ms1813678-bit/ms1813678-bit-my-business-hotel/pull/15)
- No changes have been made to `main`.
- No employee, payroll, leave, advances, authentication, model, migration, or navigation files were changed by Workstream 2.

## Existing foundation retained

The branch already contained the attendance evidence and approval foundation. Workstream 2 retained it rather than replacing it:

- `AttendanceImportBatch` preserves the source type, file hash, original filename, uploader, timestamp, status, and original file bytes.
- `AttendancePunch` stores parsed raw events with source sheet/row/event position and the raw timestamp text.
- Import batches reject duplicate source-file hashes and preserve unknown-employee / malformed-row issues.
- Program-report and fingerprint evidence remain separate. Reconciliation preserves disagreements instead of silently choosing one source.
- Daily attendance proposals are not payroll approvals. HR approval stores final clock values separately and requires a reason.
- Month attendance approval is gated by daily approval and explicit month coverage attestation.
- Security roster upload already uses a temporary staged workbook and a non-persistent preview before per-day confirmation.

The attendance import route itself still creates a persisted batch during upload; it does not yet have a non-persistent raw-file preview before batch creation.

## Workstream 2 changes

### Attendance workbook readers

- Malformed `.xlsx` containers and worksheet XML errors that happen during lazy worksheet iteration are normalized to a controlled import error.
- Malformed legacy `.xls` parsing errors are normalized after the declared `xlrd` dependency opens the workbook.
- Added regression cases for malformed containers, lazy-corrupt sheets, and invalid legacy `.xls` data.

### Security roster parser and proposal engine

- Header aliases map exactly to employee IDs; duplicate or ambiguous identities are not silently collapsed.
- Requires exactly three configured active Security employees in the parser and backend workflow.
- Distinguishes `OFF` from `00:00`.
- Recognizes `Date` / `Work Date` and known metadata headers such as `Notes` and `Total Hours`.
- Unknown, unmapped employee columns become explicit issues and block day confirmation.
- Applies the confirmed hotel rule: exactly three Security employees; each day must have two working guards on 12-hour shifts or three working guards on 8-hour shifts. One working guard is rejected; OFF is distinct from 00:00.
- Missing/ambiguous patterns, zero-length intervals, duplicate starts, and overlaps remain review blockers.

### Cross-date overnight collision guard

- Preview compares consecutive roster dates so an overnight shift ending after midnight cannot silently overlap a next-day assignment.
- Day confirmation repeats the adjacent-approved-date check at the persistence boundary, protecting against stale previews and out-of-order approval.
- The adjacent-day lookup uses Security shift-pattern ownership, so a former Security employee's previously approved shift remains evidence even after their active employee status changes.

### Attendance review hints

- The daily review page displays suggested lateness and early-departure minutes against the scheduled shift.
- Imported source evidence is not overwritten by these suggestions.

### Security monthly approval service

- Added `approve_security_roster_month` to the Security workflow module.
- It requires the first calendar day of the month and a nonblank approval reason.
- It refuses a month if any calendar date lacks a daily approval audit entry.
- It parses the immutable daily audit evidence, rejects duplicate employee IDs (including OFF entries), and checks that the non-OFF start times still match persisted Security assignments.
- It rejects unexpected extra assignments and assignments whose shift pattern belongs to another department.
- It writes an immutable `security_roster_month_approved` audit event after successful validation.
- It applies the confirmed fixed-team rule: two working guards use 12-hour shifts; three use 8-hour shifts; one working guard is rejected.

The monthly approval service is **not yet wired to an HTTP route or screen**. This requires the shared application route/template entry point, which is outside the approved Workstream 2 write boundary.

## Files in PR #15

- `app/attendance_import.py`
- `app/security_roster.py`
- `app/security_roster_import.py`
- `app/security_roster_workflow.py`
- `app/templates/attendance_review.html`
- `tests/test_attendance_import.py`
- `tests/test_security_roster_import.py`
- `tests/test_security_roster_workflow.py`

## Tests and validation

- Before the HR-confirmed fixed-three-person rule was added, 26 targeted behavior checks passed in an isolated SQLite harness. The newly added fixed-team regression tests have **not yet been executed**; do not treat the earlier 26 checks as validation of the latest changes.
- These are targeted local behavior tests, **not** the complete repository pytest suite and not a substitute for PostgreSQL integration or Windows validation.
- The container does not have `psycopg` or `xlrd`; package installation was attempted but the environment has no package-index network access. Therefore I could not execute the full project suite locally or exercise the legacy `.xls` reader locally.
- Latest inspected GitHub Actions run #699 (run ID `42367511642`) ran for the fixed-team update. Both Ubuntu/PostgreSQL and Windows jobs failed; the retry was accepted but failed again. GitHub returned empty step lists and the job-log endpoint returned 404/BlobNotFound, so there is no evidence that pytest assertions ran. The runner/service startup cause remains unknown; no CI pass is claimed.
- Current branch head still needs a real CI run with runners started and test steps executed. No Ubuntu/PostgreSQL or Windows CI pass is claimed.

## Shared-file integration work requested from Workstream 1 / coordinator

The following narrow changes are required before claiming end-to-end completion. Workstream 2 has not made these shared edits.

1. **`app/main.py::_security_roster_aliases`** — must continue to require exactly three active Security employees and exact employee mapping. The parser/backend now enforce the same fixed team rule; route remains a shared-file integration item.
2. **Security confirmation route + `app/templates/security_roster.html`** — collect a required HR reason and pass it into `confirm_security_roster_day` / the daily audit entry. The current route accepts a review checkbox only and the service currently writes a fixed daily reason.
3. **Security month approval route/template** — expose `approve_security_roster_month(db, month_start, admin, reason)` with CSRF protection, authenticated HR admin, and a required free-text reason; display month status and missing dates before allowing submission.
4. **`app/main.py::attendance_review_page` and `attendance_review.html`** — join the employee's effective department for the work date and display it. Current review query returns the attendance record and Employee, not the department.
5. **Attendance upload preview route** — stage the original file bytes temporarily, show parser issues and a source summary before creating an `AttendanceImportBatch`; only the explicit confirmation action should persist the batch. Preserve the existing SHA-256 duplicate protection and original bytes.
6. **CI/service issue** — restore usable Ubuntu and Windows runners for the existing pull-request workflow. The PR target branch does include a pull-request workflow trigger; the current failure is at job startup rather than a missing PR trigger.

### Business rule requiring hotel/HR confirmation

The HR owner confirmed the Security team is exactly three employees. Each day has either two working guards on 12-hour shifts or three working guards on 8-hour shifts. A one-guard day is prohibited. The parser and workflow now enforce the fixed team size; teams of any other size are rejected.

Still to confirm: whether a day with all three guards marked OFF is permitted, and whether shift overlaps for handover are allowed. The current implementation continues to block overlapping intervals.

## Integration status

PR #15 remains Draft and unmerged. It is mergeable according to GitHub's current metadata, but it is not approved for integration. Current GitHub comparison against Team 1's `feature/platform-hardening` baseline reports 55 commits ahead / 1 behind, with merge base `bf95f64a482a1b317ec262d44b9286c29d13b62c`. The cross-branch delta includes `.github/workflows/tests.yml` and `tests/test_attendance_acceptance.py` in addition to Workstream 2 files. Team 1 must reconcile this divergence and workflow/acceptance-test delta before retargeting or merging.

No changes were made to `main`. No successful Windows or PostgreSQL CI result is claimed.
