# Team 4 delivery notes

## Confirmed payroll policy — 2026-11-07

The HR administrator confirmed a 30-day basis for the hourly-rate calculation.
The existing formula is:

`hourly rate = basic salary / (daily working hours × 30)`

The implementation in `app/payroll_math.py` already matches this decision and was not changed.
Absence deduction remains based on the actual number of calendar days in the payroll month.
New-hire salary proration also uses actual calendar days.

## Payroll and reporting scope

This branch is based on `work/payroll-foundation` at
`d60a311ee3c22ebf06e19e3d020f6b5e4bc4fe9f`. It retains the payroll lifecycle,
payment tracking, existing Excel/PDF export, and existing dashboard foundation.

Team 4 additions include:
- Offline, deterministic, read-only payroll analysis helpers for arithmetic reconciliation,
  missing identity, duplicate employee/source IDs, invalid and negative values,
  unapproved overtime, inconsistent overtime hours/amounts, and payroll snapshot comparisons.
- A monthly Payroll comparison worksheet with headcount, gross earnings, deductions,
  net payroll, paid and remaining values, and fully/partially/unpaid counts.
- Regression tests for payroll math policy and boundaries, duplicate payroll creation,
  partial payment/report totals, and assistant analysis behavior.
- Excel formula-injection protection: user-controlled strings starting with `=`, `+`,
  `-`, or `@` (including whitespace-prefixed strings) are exported as literal text.
  Both helper-level and workbook-level regression cases cover this protection.

The assistant has no network client, no external AI calls, no database writes, no approvals,
no payments, and no automatic corrections. It is an offline analysis foundation, not a
generative model or an integrated chat interface.

## Overtime safety

The persisted payroll item model does not currently contain management approval state,
approver identity, or approval timestamp. Team 4 therefore blocks positive overtime-hour
edits in the payroll update workflow until Team 1 implements a real persisted approval flow.
The assistant flags overtime unless approval is explicitly represented as `True`, flags
negative overtime hours, and flags hours/amount mismatches. This is a fail-closed interim
safeguard, not a substitute for a manager-approval workflow.

## Team 1 integration handoff

Team 4 did not change shared application entry points, models, migrations, global settings,
or dependencies. The following work requires Team 1 ownership/review:
1. Add persisted overtime approval state, approver identity/timestamp, and protected
   approval controls before positive overtime can be re-enabled.
2. Include a protected local-only assistant router/screen behind the existing admin
   session and CSRF protections, and add its navigation entry.
3. Add dashboard financial KPIs (gross/net/paid/outstanding payroll) and advance alerts
   using agreed database queries and permissions.

The current dashboard continues to show operational workflow counts but not the
financial KPIs above. The assistant helper is not yet exposed through a route or page.

## Validation status — 2026-11-07

The latest observed Actions run for head commit
`1fde6a63901841a7a703be94f9c534b96210fcd8` is run `42364917396`.
Both Linux pytest and Windows compatibility jobs failed. A retry of both failed
jobs was accepted and also completed with failure. GitHub returned empty job-step
lists, job-log downloads returned `BlobNotFound`, and the run had no artifacts.
The failure location and root cause therefore cannot be established from the
available run data.

The branch has not been tested locally in this environment. No successful test
execution is claimed. Do not merge until CI failure diagnostics are available,
all required checks pass, and Team 1 completes the shared-surface integration
review. PR #14 remains Draft and unmerged.
