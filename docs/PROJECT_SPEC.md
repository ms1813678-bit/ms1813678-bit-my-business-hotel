# Project specification — M.Y. Business Hotel HR System

## Product goal
A local-first Arabic/English HR and payroll application for M.Y. Business Hotel, Maadi, Cairo. The system is operated by one authorized administrator: the HR Manager. It is not a public website and must not expose its local service to the public internet.

## Runtime and architecture
- Target: Windows 11 laptop, 16 GB RAM, approximately 500 GB storage.
- Local web application with PostgreSQL as the database and local file storage.
- Manual launch from a desktop shortcut; do not auto-start with Windows.
- Arabic is the default language; support English, RTL/LTR, Africa/Cairo timezone, DD/MM/YYYY, 24-hour time, and EGP.
- Core functions must work offline. AI is optional and must not block normal operation.
- Prefer a free local AI model. Do not use paid APIs, subscriptions, or external data services without explicit approval.
- Keep the architecture migration-ready for a future cloud deployment without requiring hosting now.

## Access and security
- Exactly one login: System Administrator / HR Manager. No employee, supervisor, or department-manager accounts.
- Username and password with secure password hashing; never reversible password storage.
- Keep the app local; do not expose it publicly.
- Audit consequential changes only, recording actor, timestamp, action, previous value, new value, and reason.
- Protect the laptop and external backup drive.

## Modules
1. Dashboard
2. Employees
3. Attendance
4. Leave
5. Payroll
6. Advances
7. Sanctions and rewards
8. Reports
9. Optional AI assistant
10. Settings and backup

Future modules, not part of the initial release: purchasing, inventory, accounting, maintenance, assets/custody, and expenses. Payroll must not be linked to accounting or expenses.

## Employee records
- Organize Hotel → Department → Branch → Employees.
- Editable departments: Reception, Security, Housekeeping, Maintenance, Administration, HR, Finance, Purchasing.
- Branches are configurable within departments.
- One active department and branch per employee; preserve transfer history.
- Generate employee codes MY-0001 onward; never reuse a code.
- Store full name, national ID, phone, address, optional photo, fingerprint ID, department, branch, position, hire date, status, basic salary, daily work hours, probation dates, leave balances, attendance, sanctions/rewards, and change history.
- Do not implement contract-expiry management or document uploads in the initial release.
- Allow HR to make retroactive changes with effective date, record date, and reason.
- Terminated employees are archived. Rehire uses the same code but starts a new service period while preserving previous history.
- Employee requests for personal-data changes are recorded; HR applies changes with old/new history.
- No employee master Excel file currently exists; initial records are entered manually. Provide an import template later.

## Attendance imports and review
- Monthly inputs: ZKTeco LX50 fingerprint export and a department-prepared attendance Excel file.
- Preserve original imported files and raw fingerprint events.
- Fingerprint export may include fingerprint ID, date, time, and entry/exit marker.
- Department Excel includes employee names, dates, weekly rest, and codes: H=official holiday, W=weekly rest, S=sick, A=annual, U=absence.
- A blank cell is not proof of presence or absence.
- Match by fingerprint ID or employee code. Detect missing, duplicate, unknown, and conflicting events.
- Flag entry-without-exit, exit-without-entry, missing fingerprints, duplicate events, unknown IDs, and conflicts with leave/absence.
- Never invent missing fingerprint times. Keep source-file references for later retrieval from the device.
- Monthly status: Imported → Under Review → Approved. Payroll cannot be finalized until attendance is approved.
- Unresolved absence is never classified as final absence.
- No mobile/GPS attendance, no employee self-check-in, and no individual advance shift roster.
- Support overnight shifts and midnight boundaries. Ambiguous shift patterns must be flagged, not guessed.
- Shift matching uses separately configurable before/after margins, each capped at 120 minutes; effective windows are reduced as needed so neighboring shift windows never overlap. Exact midpoint minutes between neighboring starts are left unmatched rather than guessed. Duplicate shift start times (including Security's two 08:00 starts) remain ambiguous to clock-based matching and require the applicable roster/administrator input.
- AI-assisted attendance review is advisory: it analyzes imported records, proposes matches with reasons, and presents exceptions for HR review. Clear cases may be prepared automatically under approved rules, but attendance is not finally approved and consequential records are not saved as approved without the administrator's confirmation. Core attendance workflows remain usable if AI is unavailable.
- Security monthly roster rule: the uploaded Excel roster supplies each of the three Security employees' shift start time or OFF for each date. If exactly one employee is OFF, the other two each work 12 hours. If all three work, each works 8 hours. 00:00 is a valid shift start, never an OFF marker. Derive the end time by adding the applicable duration across midnight. A day with fewer than two or more than three working employees, duplicate starts, or no unique configured start/end pattern is sent for administrator review; do not guess. AI may parse and propose the roster, but the administrator confirms before assignments are saved as approved.
- Configurable shift patterns:
  - Administration: 09:00–17:00
  - Reception: 07:00–15:00, 15:00–23:00, 23:00–07:00
  - Security: 08:00–20:00, 20:00–08:00, 08:00–16:00, 16:00–00:00, 00:00–08:00
  - Housekeeping: 08:00–16:00, 16:00–00:00, 09:00–17:00
  - Maintenance: 09:00–18:00
- Daily hours are defined by job/employee; do not assume every role is eight hours.
- Monthly standard hours = daily hours × 30.
- Grace period: 10 minutes late and 5 minutes early departure. Calculate actual net minutes/hours after grace; do not round 30 minutes to a full hour.
- Track late and early-departure counts and total minutes separately.

## Leave
- Types: annual, sick, casual/emergency, official holiday, weekly rest, unpaid.
- Initial balances are entered manually. Never invent entitlements or balances.
- Administrator manages leave directly; no employee self-service or manager approvals.
- Planned leave is normally requested 72 hours in advance; sick/emergency leave may be entered anytime with a reason.
- Deduct a balance only once leave is confirmed/recorded. Preserve balance history.
- Entitlements remain configurable.

## Payroll
- Fixed monthly base salary, not linked to hours worked. Hours are used only for agreed deductions/additions.
- Pay date: day 5 of the following month.
- Payroll fields: base salary, attendance bonus, performance bonus, occupancy bonus, overtime, other bonuses, absence deduction, lateness deduction, insurance share, taxes, other deductions, advance deduction, net pay.
- Insurance/tax fields are reserved; no automatic legal/tax calculation in the initial release.
- Bonuses and other payroll items are manual; no invented bonus formulas.
- Monthly standard hours = daily hours × 30.
- Hourly rate = base salary ÷ (daily hours × 30).
- Late/early deduction = hourly rate × actual net hours after grace.
- Absence deduction = monthly salary ÷ actual calendar days in month × absence days. Weekly rest is not deducted.
- New hire mid-month: salary ÷ actual calendar days × payable days from hire date through month end, including hire date.
- Overtime requires management-requested additional work, not voluntary staying. HR reviews at month end and decides eligible hours. Same hourly rate; no multiplier.
- One administrator previews and approves payroll. Attendance must be approved first.
- Month closes after approval. Reopening requires a reason and audit record of old/new state.
- Payment methods: cash or bank transfer. Track unpaid/partially paid/fully paid, amount, actual date, and method.
- No transport or meal allowances.

## Advances
- Requests accepted from the 15th through the 22nd of each month.
- Maximum outstanding advances after a new advance: 25% of basic salary.
- New advance is allowed only if total outstanding after it remains within the 25% limit.
- Entire advance is deducted from the following month's salary, not installments.
- Show latest advance, date, total, deducted, outstanding, and available limit.

## Sanctions and rewards
- Sanctions: verbal notice, first warning, final warning, salary deduction, suspension.
- Administrator records and approves. No employee acknowledgment workflow.
- Deduction amount is manual and flows into payroll.
- Rewards: financial reward, certificate, commendation, promotion recommendation, and configurable types. Financial amount is manual. Preserve history.

## Reports and dashboard
- Daily, weekly, monthly, and custom-range reports with filters.
- Excel and PDF exports.
- Reports: employee, attendance, leave, sanctions/rewards, payroll, advances, and department comparisons.
- Prepare previous-month report on day 1 at 09:00 Cairo time and save locally.
- Intended email recipient: mohamedhassanien398@gmail.com. Email requires internet and configured credentials; never claim delivery unless confirmed.
- Dashboard shows counts, attendance, incomplete records, leaves, payroll, alerts, department rankings, and previous-month comparisons.
- Never present unresolved absence as final.

## Optional AI assistant
- Administrator only. Free local model preferred; evaluate compatibility/performance on 16 GB Windows laptop.
- Model download may require internet once; offline operation afterward.
- System must remain fully usable when AI is disabled or unavailable.
- Natural-language assistance for employee search/analysis, Excel parsing/mapping, leave, payroll preview, advances, sanctions/rewards, and reports.
- Preview and confirm before saving. Never invent missing data, approve payroll/leave automatically, or change consequential data without administrator confirmation.
- Never guess unknown Excel columns; ask only when genuinely necessary.
- No voice AI and no API bypass.

## Backup and recovery
- External-drive backups: daily retained 40 days; weekly retained 1 year; quarterly full backups never automatically deleted.
- Full backup includes database, current/former employees, raw fingerprint imports and attendance, leave, payroll, advances, sanctions/rewards, departments/branches/positions, users/roles/settings/audit, and relationships.
- Verify integrity and perform restore tests. On failure, alert and retry; report success only after verification.
- Restore requires sole administrator confirmation, displays backup date, records reason, and preserves current state first when possible.

## Delivery principles
- Implement in small, reviewable stages.
- Do not claim a feature is complete until code and tests support that claim.
- Preserve source imports and historical records; use database migrations for schema changes.
- Add automated tests for payroll calculations, date boundaries, duplicate imports, permissions, and backup/restore before production use.
- No production deployment or real employee data until local installation, security, backup, and restore have been tested on the target laptop.
