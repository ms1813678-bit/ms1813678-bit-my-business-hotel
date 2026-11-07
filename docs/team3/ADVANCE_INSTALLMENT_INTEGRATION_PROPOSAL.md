# Shared integration proposal — salary advance installments

**Status:** Proposal only. Team 3 has not changed shared models, migrations, routes, or payroll processing.

## Why a shared change is required

The current `AdvanceRequest` stores one principal amount, one payroll month, and a single `deducted_at` timestamp. The current advance workflow treats the whole paid advance as outstanding until a single payroll deduction is recorded. This cannot represent six separately dated deductions or accurately reduce outstanding principal after each successful installment.

## Proposed persistence model

Add an `AdvanceInstallment` child table, subject to Team 1 schema review:

- `id` primary key
- `advance_request_id` foreign key to `advance_requests.id`
- `installment_number` positive integer
- `payroll_month` date (first day of the salary period)
- `due_date` date (the fifth of the following calendar month)
- `amount` numeric(12,2), positive
- `status` enum/string: scheduled, applied, reversed, cancelled
- `payroll_run_id` nullable foreign key/reference to the exact payroll run that applied it
- `applied_at`, `reversed_at`, `created_at`
- immutable audit trail for creation, application, reversal and cancellation

Required database constraints:
- unique (`advance_request_id`, `installment_number`)
- unique payroll consumption identity for an installment (at minimum, one successful application per installment; payroll-run retries must be idempotent)
- positive amount and installment number constraints where supported

A payroll run must consume only scheduled installments whose `payroll_month` equals the run's salary month. It must not infer the salary month from the calendar payout date.

## Approved date convention

An advance requested on 15 September belongs to September payroll, which is paid on 5 October. With EGP 6,000 over six installments:

| # | Payroll month | Due/pay date | Amount |
|---:|---|---|---:|
| 1 | September | 5 October | 1,000 |
| 2 | October | 5 November | 1,000 |
| 3 | November | 5 December | 1,000 |
| 4 | December | 5 January | 1,000 |
| 5 | January | 5 February | 1,000 |
| 6 | February | 5 March | 1,000 |

The request window remains 15–22. The 25% basic-salary cap remains on outstanding principal. Outstanding principal should equal the original principal less successfully applied (not merely scheduled) installments. Pending and approved requests reserve their full principal. Rejected/cancelled requests do not reserve principal. A paid-to-employee advance remains outstanding until payroll deductions have actually been applied.

## Payroll contract requested from Team 4

For a given payroll run and employee:
1. Read only scheduled installments for the run's salary month.
2. Include each installment exactly once in payroll deductions.
3. Persist the installment-to-payroll-run association atomically with payroll finalization.
4. Repeated preview/finalize requests must not create a second deduction.
5. A failed/rolled-back payroll run must not leave an installment marked applied.
6. Reopening/reversal must create an auditable reversal; never erase the original application.
7. Return advance ID, installment ID/number, payroll month, due date, amount, and status to the HR audit/report view.

## Approval and safety

This proposal requires Team 1 approval for model/migration changes and Team 4 approval for payroll-run integration. Until both are implemented and tested together, installment schedules remain planning data only; do not expose them as completed payroll deductions. No shared code was changed by Team 3 in this proposal.
