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
language model or a complete chat UI.

## Integration boundary / team 1 handoff

The assistant router and navigation are not wired into `app/main.py`, because that
file is a team 1 shared file and is outside team 4's permitted write scope.
Team 1 must include the future assistant router behind the existing
`get_current_admin` dependency and CSRF protection, and add its navigation entry.
No models, migrations, global settings, or shared dependencies were changed here.

## Validation status

The prior PR #12 checks were observed failing on both the pytest and Windows
compatibility jobs. The workflow logs were not retrievable through the connected
GitHub interface during this handoff. New assistant tests are included in
`tests/test_payroll_assistant.py`; they have not been executed in this environment.
