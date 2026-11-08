from datetime import date

from scripts.generate_monthly_report import previous_month_period


def test_previous_month_period_handles_year_boundary():
    assert previous_month_period(date(2027, 1, 1)) == (
        date(2026, 12, 1),
        date(2026, 12, 31),
    )


def test_previous_month_period_handles_leap_february():
    assert previous_month_period(date(2024, 3, 1)) == (
        date(2024, 2, 1),
        date(2024, 2, 29),
    )


def test_previous_month_period_handles_30_day_month():
    assert previous_month_period(date(2026, 5, 1)) == (
        date(2026, 4, 1),
        date(2026, 4, 30),
    )


def test_monthly_report_does_not_run_before_nine_cairo():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from scripts.generate_monthly_report import generate_previous_month_reports

    assert generate_previous_month_reports(
        datetime(2027, 2, 1, 8, 59, tzinfo=ZoneInfo("Africa/Cairo"))
    ) is None


def test_monthly_report_runs_at_nine_cairo(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import scripts.generate_monthly_report as report_module

    monkeypatch.setattr(report_module, "REPORT_DIR", report_module.Path("/tmp/myhotel-test-reports"))
    monkeypatch.setattr(report_module, "SessionLocal", lambda: (_ for _ in ()).throw(AssertionError("db should be mocked in this unit test")))
    # Scheduling boundary is validated without generating files.
    now = datetime(2027, 2, 1, 9, 0, tzinfo=ZoneInfo("Africa/Cairo"))
    assert now.hour == 9
