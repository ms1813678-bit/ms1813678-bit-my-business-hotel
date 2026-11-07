# AI Workstream Handoff Briefs
# M.Y. Business Hotel HR & Payroll System

Baseline for all three specialist workstreams:
- Repository: `ms1813678-bit/ms1813678-bit-my-business-hotel`
- Baseline commit: `539272d1ce7c500c93c1ebec9aed19e2a293117f`
- Read first: `docs/PROJECT_COORDINATION.md`
- Do not edit `main`, do not merge PRs, do not force-push, and do not modify shared files outside your scope.
- Each specialist must inspect the current branch contents before coding. Do not recreate features already present.
- No real employee data. Use synthetic fixtures only.
- Submit a PR targeting `feature/attendance-end-to-end-acceptance` when the assigned scope is ready. The coordinator alone reviews and merges.

## Specialist 2 — Attendance audit and gap completion

Assigned branch: `work/attendance-review`

You are responsible only for auditing and completing the existing attendance/security-roster implementation. This is not a greenfield rewrite.

First inspect:
- `app/attendance_import.py`
- `app/attendance_reconciliation.py`
- `app/attendance_workflow.py`
- `app/shift_matching.py`
- `app/security_roster_import.py`
- `app/security_roster_workflow.py`
- attendance/security roster templates
- `tests/test_attendance_*.py`
- `tests/test_security_roster*.py`
- `tests/test_shift_matching.py`
- `docs/ATTENDANCE_SHIFT_DESIGN.md`

Tasks:
1. Produce a route → service → model → test map of existing behavior.
2. Identify missing acceptance cases without rewriting working code.
3. Verify source priority: program report primary, fingerprint export secondary.
4. Verify preservation of original workbook bytes, raw timestamps, all time tokens, sheet/row/event provenance.
5. Verify exact identity matching only; unknown IDs remain unresolved.
6. Verify duplicates, missing entry/exit, source conflicts, leave conflicts, and missing coverage are never silently converted to absence.
7. Verify day approval and separate monthly approval, including coverage attestation and audit records.
8. Verify Security roster rules: three employees, OFF handling, 00:00 is a valid time, 12-hour shifts when one is OFF, 8-hour shifts when all three work, invalid roster rows require review.
9. Verify overnight and midnight boundaries and ambiguous shifts are not guessed.
10. Add only missing tests/fixes inside attendance-owned paths. Shared schema or `app/models.py` changes must be submitted as proposals to the coordinator; do not edit migrations.
11. Report the real hotel workbook and target Windows 11 tests as external acceptance gates, not completed.

Required handoff: changed paths, route/service/model map, missing cases, exact test command and pass/fail counts, unresolved limitations, confirmation no shared files were changed.

## Specialist 3 — Leave, advances, sanctions, rewards

Assigned branch: `work/leave-advances-rewards`

You own only the new leave, advances, sanctions, and rewards modules. Do not edit employee core models, attendance, payroll, shared layout/navigation, `app/main.py`, `app/models.py`, or `alembic/**`.

Tasks:
1. Inspect the current model and application patterns before designing new modules.
2. Implement administrator-managed leave types: annual, sick, casual/emergency, official holiday, weekly rest, unpaid.
3. Support manually entered opening balances and a complete immutable/auditable balance-change history.
4. Deduct balances only once when leave is confirmed/recorded; support reasoned sick/emergency entries at any time and normally 72-hour advance entry for planned leave.
5. Implement advances with the 25% basic-salary outstanding cap: a new advance is allowed only when total outstanding after granting remains within the cap; the full amount is due from the following month's payroll, not installments.
6. Implement sanctions: verbal notice, first warning, final warning, salary deduction, suspension. Financial deductions are manually entered.
7. Implement rewards: financial reward, certificate, commendation, promotion recommendation, and configurable types. Financial amount is manually entered.
8. Preserve employee-level history and audit events.
9. Build domain services and focused tests. Do not integrate deductions into payroll yourself; provide a documented service/API contract and test fixtures for Workstream 4.
10. If tables/columns are needed, write a migration proposal document only. The coordinator will create/review migrations and shared model changes.

Required handoff: domain/API contract, proposed schema, changed paths, test results, edge cases, and confirmation no shared files were edited.

## Specialist 4 — Payroll, reports/dashboard, optional local AI

Assigned branch: `work/payroll-reports-ai`

You own payroll workflow, reports/export, dashboard aggregation, and the optional local AI adapter. Do not edit attendance, employee core models, shared layout/navigation, `app/main.py`, `app/models.py`, or `alembic/**`.

Tasks:
1. Inspect existing `app/payroll_math.py` and `tests/test_payroll_math.py`; preserve and extend existing helper behavior rather than duplicating it.
2. Do not finalize or change the monthly divisor until the coordinator resolves the documented conflict between the current spec's daily-hours × 30 formula and the later 40-day instruction. You may build the workflow behind a clearly isolated configurable policy, but do not select a value or silently change calculations.
3. Implement a complete payroll lifecycle: period, preview, line items, approval, close/reopen with reason, and payment tracking (cash/bank, unpaid/partial/paid, amount, actual date).
4. Payroll must be blocked until attendance is approved for the period.
5. Include manual bonuses, manual insurance/tax values, other deductions, approved overtime only, absence/lateness deductions, and advance deduction contracts from Workstream 3. Do not invent bonus or legal/tax formulas.
6. Overtime is payable only when management requested/approved it.
7. Salary payment target is day 5 of the following month.
8. Implement daily/weekly/monthly/custom-range reports, department comparisons, and Excel/PDF exports. Prepare and save the previous-month report locally on day 1 at 09:00 Africa/Cairo; email is optional and must never be claimed sent without a confirmed result.
9. Dashboard metrics must come from persisted data, not hard-coded sample counts.
10. Optional local/free AI only: text interface, admin-only, preview and explicit confirmation before changes, no automatic approvals, and all core workflows continue when AI is disabled or unavailable.
11. Provide domain/schema proposals to the coordinator instead of editing shared models/migrations.

Required handoff: payroll service contract, report/export examples using synthetic data, AI offline/fallback behavior, changed paths, exact test results, and unresolved policy blockers.

## Shared stop-work rule

If a task requires a shared file or a rule not settled by the project specification, stop before editing that file or making that business decision. Send the coordinator a concise proposal with alternatives, consequences, and tests needed. Do not guess.
