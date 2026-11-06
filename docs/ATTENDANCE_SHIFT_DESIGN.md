# Attendance shift design and implementation notes

## Confirmed operating rules

- Security uses the monthly security roster supplied by HR. Security shifts are not inferred from clock events.
- Other departments use the first actual clock-in event to identify a shift, but only against that department's configured shift patterns.
- HR configures the matching window in minutes. A missing window disables automatic matching; no default tolerance is silently assumed.
- If more than one shift is within its configured window, or none is within range, the result is sent for human review. The application does not choose the nearest shift when multiple patterns qualify.
- Overnight shifts remain associated with the work date on which the shift starts.
- Missing or uncertain fingerprint events are never fabricated.

## Approved shift patterns

| Department | Shift times |
|---|---|
| Administration | 09:00–17:00 |
| Reception | 07:00–15:00; 15:00–23:00; 23:00–07:00 |
| Security | 08:00–20:00; 20:00–08:00; 08:00–16:00; 16:00–00:00; 00:00–08:00 |
| Housekeeping | 08:00–16:00; 16:00–00:00; 09:00–17:00 |
| Maintenance | 09:00–18:00 |

## Current code scope

- `ShiftPattern` stores department-specific shift definitions and optional matching windows.
- `SecurityShiftAssignment` stores one security employee's shift per work date.
- Alembic revision `0007_attendance_shift_foundation` creates these tables without modifying prior migrations.
- `scripts/seed_shifts.py` inserts the approved shift definitions idempotently. It deliberately leaves matching windows unset until HR configures them.
- `app.shift_matching.match_shift_by_clock` is a pure matching helper. It returns matched, unmatched, or ambiguous; it does not alter raw fingerprint timestamps.

## Not yet implemented in this stage

The security roster upload/editor, attendance import and reconciliation, monthly attendance review/approval, and payroll gate belong to the following attendance stages. This foundation does not claim those workflows are complete.
