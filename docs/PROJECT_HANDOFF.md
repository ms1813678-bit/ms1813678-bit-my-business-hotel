# Project handoff and current decisions

## Current integration branch
- Branch: integration/release-hardening-v2
- Target branch: main
- Do not merge to main until real CI execution is available and Windows 11 + PostgreSQL acceptance is completed.

## Confirmed business decisions
1. Reports are manual only. The administrator chooses start date, end date, and either all employees or one employee. Excel/PDF are generated locally. No automatic day-1 scheduler or automatic email delivery in the initial release.
2. Regular monthly advances alone are limited to 25% of basic salary.
3. Installment/large advances are independent of the 25% limit and may use 1–36 installments.
4. Advance installment accounting starts in the request month; payout date is the 5th of the following month.
5. Payroll advance deductions consume the installment ledger, never the full advance amount. Closing applies installments; reopening reverses them.
6. Positive overtime requires explicit administrator approval. Editing overtime hours clears prior approval and requires re-approval.
7. Security attendance requires at least two active guards every day. Exactly two guards use 12-hour coverage; three or more use 8-hour coverage. Configured shift times are authoritative; gaps and overlaps are rejected.
8. Attendance remains evidence-first: source files are retained, daily records require HR approval, and monthly approval requires both source imports and explicit coverage confirmation.
9. Employee codes are never reused. Rehire preserves the code but creates a new service period.

## Latest integration checkpoint (2026-11-08)
- Windows setup, README, and Windows installation guide now state that reports are generated manually and saved locally; setup does not register a monthly report task.
- Project specification reflects the approved advance split: regular monthly advances alone are capped at 25%; installment advances are exempt and support 1–36 installments.
- Added route-level synthetic acceptance coverage for a 6,000 installment advance over six installments, including request-month accounting, next-month day-5 payment dates, and exact schedule total across a year boundary. The test is committed but has not yet been executed locally or in a trustworthy CI run.
- The installment migration (0021) creates unique per-request installment number and payroll-month constraints and intentionally preserves financial history on downgrade. Migrations 0022 and 0023 add advance type and overtime approval metadata. These still require execution against a clean PostgreSQL database.
- The current test workflow targets Python 3.11 on Ubuntu and Windows; the latest job API results still expose no step details/logs. Do not infer the cause of failure or claim tests pass.
- PR #20 remains Draft and unmerged; main remains unchanged. Team 3 PR #17 must not be merged wholesale because its stated 25% policy conflicts with the approved rule and its branch is divergent.

## Verification status
- GitHub Actions has previously reported failures before project steps execute (steps null, no logs), so those runs are not valid evidence of pytest failure.
- Real Windows 11 + PostgreSQL acceptance has not yet been performed.
- Do not claim release readiness until migrations, tests, backup/restore, and Windows acceptance are actually executed.

## Current integration focus
- Advance installment ledger and payroll lifecycle.
- Manual report filters.
- Overtime approval gate.
- Security roster validation.
