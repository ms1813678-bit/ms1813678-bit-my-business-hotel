"""Scheduled backup entry point. Run from Windows Task Scheduler."""
import argparse
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.backup_workflow import create_backup
from app.db import SessionLocal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=["daily", "weekly", "quarterly", "manual"], required=True)
    args = parser.parse_args()

    log_dir = ROOT / "var" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_dir / "backup.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    for attempt in range(1, 4):
        try:
            with SessionLocal.begin() as db:
                run = create_backup(db, args.type, actor="windows-backup-scheduler")
                if run.status == "success":
                    logging.info(
                        "Backup verified: type=%s path=%s sha256=%s bytes=%s",
                        run.backup_type, run.archive_path, run.sha256, run.size_bytes,
                    )
                    return 0
                error = run.error_message or "Backup failed without an error message."
                logging.error("Backup attempt %s/3 failed: %s", attempt, error)
        except Exception:
            logging.exception("Backup attempt %s/3 raised an unexpected error.", attempt)
        if attempt < 3:
            time.sleep(60)

    logging.critical("Backup failed after three attempts. Check the external drive and PostgreSQL client tools.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
