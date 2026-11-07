# Team 3 — Salary advance repayment policy

## Supported repayment choices

HR may record either:
- **One-time deduction:** one installment for the full approved advance amount.
- **Installments:** a whole-number installment count from 1 through 36, selected by HR.

The existing request window remains day 15 through day 22. The existing 25% basic-salary limit remains a limit on the employee's total outstanding advance principal; installment selection does not increase that cap.

## Payroll-month and payment-date convention

The first installment belongs to the payroll month in which the advance is requested. Its cash payroll payout/deduction date is the 5th of the following calendar month. Subsequent installments belong to consecutive payroll months and are paid/deducted on each following month's 5th.

Example: an advance of EGP 6,000 requested on 15 September with 6 installments:
| Installment | Payroll month | Payroll payout/deduction date | Amount |
|---:|---|---|---:|
| 1 | September | 5 October | EGP 1,000 |
| 2 | October | 5 November | EGP 1,000 |
| 3 | November | 5 December | EGP 1,000 |
| 4 | December | 5 January | EGP 1,000 |
| 5 | January | 5 February | EGP 1,000 |
| 6 | February | 5 March | EGP 1,000 |

The payroll month is not the same as the cash payout date. For example, the first row is a September payroll deduction paid on 5 October.

Amounts are calculated to two decimal places. Any indivisible remainder is distributed one cent at a time across the earliest installments so the schedule total always equals the approved advance amount.

## Required persistence and payroll integration — shared-owner approval needed

The current `AdvanceRequest` model stores one amount and one payroll month, with a single `deducted_at` timestamp. It cannot represent multiple independently consumed installments. The payroll PR #12 currently describes carrying approved advances into a payroll month as a whole amount. Therefore production installment deductions must not be enabled until the shared-schema and payroll owners approve and implement:

- An immutable installment ledger linked to the advance, with installment number, payroll month, due date, amount, and status.
- A unique idempotency key/constraint for each advance installment and payroll run so retries cannot deduct twice.
- Per-installment applied timestamp and payroll-run reference; a paid installment cannot be consumed again.
- Cancellation/reversal rules that preserve the historical ledger and audit trail.
- Outstanding principal calculated as original principal less successfully consumed installments, while pending/approved advances continue to reserve the existing 25% cap.
- Payroll run preview, approval, close, reopen, and reports to display the installment amount for the matching payroll month only.

Until that integration is completed, this document and the pure schedule calculator are specification/test support only; the existing live payroll behavior must not be represented as installment-capable.
