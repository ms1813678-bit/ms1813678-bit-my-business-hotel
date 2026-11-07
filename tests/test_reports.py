"""Tests for the multi-sheet Excel reporting export."""
from datetime import date
from io import BytesIO

import pytest
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.reports import build_hr_workbook, validate_workbook


def test_hr_report_contains_all_expected_sheets():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            content = build_hr_workbook(db, date(2026, 10, 1), date(2026, 10, 31))
        names = validate_workbook(content)
        assert names == [
            "Employees",
            "Attendance",
            "Leave",
            "Leave balances",
            "Sanctions rewards",
            "Advances",
            "Payroll",
            "Departments",
        ]
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
        try:
            assert workbook["Employees"].freeze_panes == "A2"
            assert workbook["Payroll"].max_row == 1
            assert workbook["Attendance"].max_row == 1
        finally:
            workbook.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_hr_report_rejects_reversed_date_range():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            with pytest.raises(ValueError, match="End date"):
                build_hr_workbook(db, date(2026, 11, 1), date(2026, 10, 1))
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
