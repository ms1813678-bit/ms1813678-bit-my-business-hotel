# Local backup and restore (Windows PowerShell)

These scripts are a first implementation of a local recovery procedure. They have **not yet been executed against the target Windows 11 computer or a real PostgreSQL instance**; treat them as unverified until acceptance testing is completed.

## What is backed up

- PostgreSQL database in custom pg_dump format (database.dump).
- The complete var/employee_photos directory, kept alongside the database dump.
- A manifest with database name, timestamp, and SHA-256 checksum of the database dump.

The backup deliberately excludes .env, the session secret, database passwords, logs, temporary attendance uploads, and security-roster staging files. Keep the .env file and recovery credentials in a separate secure location. A database dump alone is not a complete recovery point because employee photos are stored on disk.

## Requirements

- Windows PowerShell 5.1 or later.
- PostgreSQL client tools (pg_dump, pg_restore, psql) installed and available in PATH.
- A valid project .env containing DATABASE_URL=postgresql+psycopg://...
- Stop the HR application before starting backup or restore. Do not edit employee records during either operation.

## Create a backup

From the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\backup_database.ps1
```

Optional destination:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\backup_database.ps1 -BackupRoot "D:\MY-HR-Backups"
```

A timestamped folder is created under backups by default. Copy completed backups to a separate physical drive. Protect that drive with BitLocker or equivalent encryption and restrict access; backups contain sensitive employee and payroll information.

## Restore a backup

1. Stop the HR application.
2. Prepare an **empty PostgreSQL database** with the same database name as the backup, and configure the local .env to point to it.
3. Keep a separate copy of the current .env and session secret.
4. Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\restore_database.ps1 -BackupFolder "D:\MY-HR-Backups\MY-HR-YYYYMMDD-HHMMSS"
```

5. Type the exact confirmation requested by the script. The script verifies the dump checksum and refuses a non-empty database.
6. Start the application and verify login, employee list, former employees, attendance history, and employee photos.

The restore script preserves the previous employee-photo folder as var\employee_photos.before-restore after a successful restore. Do not delete it until the restored system has been checked. If database restore fails, the script attempts to put the previous photo folder back.

## Acceptance tests still required

- Create a backup from a disposable PostgreSQL database with test employees and photos.
- Verify the dump with pg_restore --list.
- Restore into a fresh empty database and compare employee counts, service-period counts, attendance records, and photo checksums.
- Test a corrupted dump and confirm checksum validation stops restore before any database change.
- Test restore against a non-empty database and confirm it refuses.
- Verify the scripts under the actual Windows PowerShell version and PostgreSQL version installed on the target PC.

Do not use real employee/payroll data until these tests pass and the backup destination is protected.


### Local database safety restriction

The backup and restore scripts intentionally accept only a PostgreSQL host of `127.0.0.1`, `localhost`, or `::1`. They reject remote database hosts so a local recovery operation cannot accidentally export or overwrite a remote database. The scripts also require a non-empty password and pass `--no-password` to PostgreSQL client tools so an unexpected interactive password prompt cannot stall the operation.
