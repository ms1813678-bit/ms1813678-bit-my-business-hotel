"""Cross-platform pytest runner that preserves console output as downloadable CI artifacts."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    artifact_dir = Path("artifacts")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    log_path = artifact_dir / "pytest.log"
    report_path = artifact_dir / "pytest-report.xml"

    command = [
        sys.executable,
        "-m",
        "pytest",
        "-v",
        *sys.argv[1:],
        f"--junitxml={report_path.as_posix()}",
    ]

    print("Running:", " ".join(command), flush=True)
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                log_file.write(line)
                log_file.flush()
            return_code = process.wait()
            log_file.write(f"\npytest exit code: {return_code}\n")
        print(f"Pytest log: {log_path}", flush=True)
        print(f"JUnit report: {report_path}", flush=True)
        return return_code
    except Exception as exc:
        message = f"CI pytest runner error: {type(exc).__name__}: {exc}\n"
        with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
            log_file.write(message)
        sys.stderr.write(message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
