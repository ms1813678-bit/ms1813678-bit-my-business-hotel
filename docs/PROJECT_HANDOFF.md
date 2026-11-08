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

## Verification status
- GitHub Actions has previously reported failures before project steps execute (steps null, no logs), so those runs are not valid evidence of pytest failure.
- Real Windows 11 + PostgreSQL acceptance has not yet been performed.
- Do not claim release readiness until migrations, tests, backup/restore, and Windows acceptance are actually executed.

## Current integration focus
- Advance installment ledger and payroll lifecycle.
- Manual report filters.
- Overtime approval gate.
- Security roster validation.
