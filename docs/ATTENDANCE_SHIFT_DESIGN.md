# Attendance shift design and implementation notes

## Confirmed operating rules

- Security uses the monthly security roster supplied by HR. Security shifts are not inferred from clock events.
- Other departments use the first actual clock-in event to identify a shift, but only against that department's configured shift patterns.
- Matching uses separate before/after margins, each capped at 120 minutes. Effective windows are trimmed around neighboring shift starts to prevent overlap; the exact midpoint minute is deliberately unmatched.
- Duplicate shift start times remain ambiguous and require the security roster or HR review; the matcher never chooses between two shifts that start at the same time.
- Overnight shifts remain associated with the work date on which the shift starts.
- Missing or uncertain fingerprint events are never fabricated.

- Security roster rule: the Excel roster gives each of the three employees' shift start time or OFF. Exactly two working employees means 12 hours each; all three working means 8 hours each. 00:00 is a valid start time. End times are derived by adding the duration across midnight. Invalid working counts, duplicate starts, and missing/ambiguous configured patterns require administrator review.

## Approved shift patterns

| Department | Shift times |
|---|---|
| Administration | 09:00–17:00 |
| Reception | 07:00–15:00; 15:00–23:00; 23:00–07:00 |
| Security | 08:00–20:00; 20:00–08:00; 08:00–16:00; 16:00–00:00; 00:00–08:00 |
| Housekeeping | 08:00–16:00; 16:00–00:00; 09:00–17:00 |
| Maintenance | 09:00–18:00 |

## Current code scope

- `ShiftPattern` stores department-specific shift definitions, separate before/after matching margins, and the legacy `match_window_minutes` field for migration compatibility. The matcher ignores the legacy field.
- `SecurityShiftAssignment` stores one security employee's shift per work date.
- Alembic revision `0007_attendance_shift_foundation` creates the shift foundation; `0008_shift_matching_margins` adds bounded before/after margins and migrates explicitly configured legacy windows.
- `scripts/seed_shifts.py` inserts the approved shift definitions idempotently. New matching margins default to the approved 120-minute maximum and are trimmed by the matcher to prevent overlap.
- `app.shift_matching.match_shift_by_clock` is a pure matching helper. It returns matched, unmatched, or ambiguous; it does not alter raw fingerprint timestamps.

## AI-assisted review principle

AI may analyze attendance evidence and prepare shift suggestions with explanations. Clear cases can be prepared under approved rules; uncertain cases are presented to HR with the proposed result and reason. The administrator must confirm before consequential attendance records are approved or saved as final. AI is optional and must not block normal operation.

## Not yet implemented in this stage

The security roster upload/editor, attendance import and reconciliation, monthly attendance review/approval, and payroll gate belong to the following attendance stages. This foundation does not claim those workflows are complete.


## Attendance evidence foundation (revision 0010)

The repository now includes an initial, non-approving evidence layer:

- `AttendanceImportBatch` records source type, period, file hash, uploader, counts, and review status.
- `AttendancePunch` stores each parsed event with its exact source sheet, row, event index, raw timestamp text, and exact employee mapping when available.
- `AttendanceDailyRecord` is the future employee/work-date reconciliation record. It defaults to `needs_review`; this table is not a payroll approval.
- `app.attendance_import.parse_attendance_workbook` reads both legacy `.xls` and modern `.xlsx` files. Program exports contribute separate clock-in/clock-out evidence; fingerprint exports retain every time token in a multi-time cell.
- Employee matching is exact against configured employee codes, fingerprint IDs, or names. Unknown identities and malformed values become issues, never fuzzy matches.
- `app.attendance_workflow.persist_attendance_import` stores parsed evidence and an immutable AuditLog event, rejects duplicate source-file hashes, and does not approve daily attendance.

This is not yet the completed attendance workflow. Remaining work includes a protected upload/preview interface, program-report plus fingerprint reconciliation across the full month, shift-aware overnight pairing, review/adjustment screens, explicit monthly HR approval, and a payroll gate. Do not use imported evidence for payroll until those controls and end-to-end tests are implemented.
