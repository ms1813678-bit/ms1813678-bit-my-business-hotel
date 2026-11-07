# Team 4 delivery notes

## Confirmed payroll-rate decision — 2026-11-07

The HR administrator confirmed a **30-day basis** for the hourly-rate calculation.
The existing implementation in `app/payroll_math.py` already calculates:

`hourly rate = basic salary / (daily working hours × 30)`

No change to that formula is required. The payroll screen already displays this basis.
The separate absence-deduction calculation currently uses the actual number of calendar
days in the payroll month; this was not changed by the hourly-rate decision.

## Existing implementation retained

The work starts from `work/payroll-foundation` at
`d60a311ee3c22ebf06e19e3d020f6b5e4bc4fe9f`, which contains the existing payroll
workflow, payment tracking, Excel/PDF reports, dashboard metrics, and monthly report
scheduler. No existing payroll implementation was replaced.

## Offline assistant foundation

`app/payroll_assistant.py` provides local, read-only payroll arithmetic checks,
duplicate source-ID detection, snapshot comparison, and Arabic/English command
classification. It has no network client and cannot change or approve payroll data.
This is a deterministic offline assistant foundation, not a connected generative
language model or a complete chat UI. Currency rounding uses the same ROUND_HALF_UP
policy as the payroll math module.

## Additional regression coverage

`tests/test_payroll_math_policy.py` adds regression cases for the confirmed 30-day
hourly-rate basis, per-day lateness/early-departure grace, calendar-day absence
deduction, new-hire proration, and invalid inputs. These are test additions only;
they have not yet been confirmed passing by a successful CI run.

## Additional coverage and report enhancement

The team 4 branch adds a monthly **Payroll comparison** worksheet alongside
the employee-level Payroll sheet. It summarizes each payroll run by employee
count, gross earnings, deductions, net payroll, paid/remaining amounts, and
fully/partially/unpaid employee counts. Payment totals are linked to each payroll
item, and the comparison is calculated from the same selected payroll runs.
A synthetic regression case checks partial payment amounts and status counts.

The payroll route acceptance test also verifies that a second request for
the same month is rejected and does not create a duplicate run. Boundary tests
cover invalid months, negative minute/day inputs, and a hire on the final
calendar day. These tests remain unverified until CI succeeds.

## Overtime approval safety finding — 2026-11-07

The current payroll schema stores overtime hours and amount but has no persisted
management approval state or approver identity. Until Team 1 adds and integrates
that approval workflow in the shared model and route, Team 4 blocks positive
overtime-hour edits in the payroll workflow. The offline assistant also flags
overtime unless approval is explicitly represented as `True`. This is a
fail-closed interim safeguard, not a replacement for a real approval workflow.
A route regression test verifies that an unapproved overtime submission is
rejected without changing the payroll item. Positive overtime remains unavailable
until the approval data and controls are implemented and reviewed.

## Integration boundary / team 1 handoff

The assistant router and navigation are not wired into `app/main.py`, because that
file is a team 1 shared file and is outside team 4's permitted write scope.
Team 1 must include the future assistant router behind the existing
`get_current_admin` dependency and CSRF protection, and add its navigation entry.
No models, migrations, global settings, or shared dependencies were changed here.

The existing dashboard template exposes operational counts (active/former
employees, attendance review, leave drafts, pending advances, staff-action
drafts, and open payroll runs) but does not yet expose payroll financial KPIs
such as monthly net payroll, paid/unpaid totals, or pending advance alerts.
Because the dashboard route and template are part of the shared application
surface, Team 1 must integrate these indicators after agreeing the required
queries and permissions with Team 4. The assistant also remains unintegrated:
it needs a local-only HR screen/route and explicit confirmation controls before
it can be considered a usable assistant feature.

## Validation status — updated 2026-11-07

The latest Actions run for commit `d9005463d44591f07167f235f43c125af2ee0d43`
is run `42363551615`. Both Linux pytest and Windows compatibility jobs completed
with failure. A retry of both failed jobs was accepted by GitHub, but the retried
jobs again completed with failure. The job-steps endpoint returned empty step
lists, and the log endpoint is unavailable (`BlobNotFound`); therefore the
underlying cause remains unknown and must not be guessed.

No successful test execution is claimed. Tests have not been run locally in this
environment. The branch is not validated or ready to merge until CI failures
can be diagnosed and all required checks pass.