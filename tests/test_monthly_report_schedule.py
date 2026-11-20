"""Tests for the manual report CLI contract."""
from scripts.generate_monthly_report import main


def test_manual_report_rejects_reversed_range(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["generate_monthly_report.py", "--start-date", "2026-11-30", "--end-date", "2026-11-01"],
    )
    try:
        main()
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected argparse to reject a reversed range")
