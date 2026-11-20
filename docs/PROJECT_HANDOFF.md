# Project handoff and current decisions

## Current integration branch
- Branch: integration/final-acceptance-v1
- Source baseline: integration/continue-safe-v1
- Target branch: main
- This is the controlled final-acceptance track. Do not merge it to main until all acceptance gates are green and the owner explicitly approves the merge.

## Confirmed business decisions
1. Reports are manual only.
2. Regular monthly advances alone are limited to 25% of basic salary.
3. Installment/large advances are independent of the 25% limit and may use 1–36 installments.
4. Advance installment accounting starts in the request month; payout date is the 5th of the following month.
5. Payroll advance deductions consume the installment ledger, never the full advance amount. Closing applies installments; reopening reverses them.
6. Positive overtime requires explicit administrator approval.
7. Security attendance requires at least two active guards every day. Exactly two guards use 12-hour coverage; three or more use 8-hour coverage.
8. Attendance remains evidence-first: source files are retained, daily records require HR approval, and monthly approval requires both source imports and explicit coverage confirmation.
9. Employee codes are never reused. Rehire preserves the code but creates a new service period.

## Final acceptance gates
1. Full automated test suite passes on the final-acceptance branch, including PostgreSQL migration/concurrency tests.
2. Windows runner installation/compatibility passes.
3. Real Windows 11 + PostgreSQL installation is executed on the hotel target PC.
4. Backup creation, integrity validation, restore, and rollback behavior are executed against the target PostgreSQL environment and backup drive.
5. Synthetic end-to-end business flow passes: employee/service period -> attendance -> security roster -> leave -> advance/installment ledger -> sanctions/rewards -> payroll -> payment -> reports -> backup/restore.
6. AI assistant approval-gate tests pass and the application remains usable with local AI unavailable.
7. Arabic/English and RTL UI regression is checked.
8. Only after all gates pass may this branch be merged to main with explicit owner approval.

## Verification notes
- PostgreSQL migration tests are in tests/test_postgres_migrations.py.
- No production data should be entered before final acceptance.
- main remains unchanged by this acceptance track.
