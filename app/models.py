from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, Time, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    employees: Mapped[list["Employee"]] = relationship(back_populates="department")


class Branch(Base):
    __tablename__ = "branches"
    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    department: Mapped[Department] = relationship()
    __table_args__ = (UniqueConstraint("department_id", "name"),)


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200), index=True)
    national_id: Mapped[str | None] = mapped_column(String(20), unique=True)
    phone: Mapped[str | None] = mapped_column(String(30))
    address: Mapped[str | None] = mapped_column(Text)
    photo_path: Mapped[str | None] = mapped_column(String(255))
    fingerprint_id: Mapped[str | None] = mapped_column(String(50), unique=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"))
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"))
    position: Mapped[str] = mapped_column(String(120))
    hire_date: Mapped[date] = mapped_column(Date)
    probation_start_date: Mapped[date | None] = mapped_column(Date)
    probation_end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="active")
    basic_salary: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    daily_hours: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    department: Mapped[Department] = relationship(back_populates="employees")

  
class ShiftPattern(Base):
    __tablename__ = "shift_patterns"
    __table_args__ = (
        UniqueConstraint("department_id", "code", name="uq_shift_patterns_department_code"),
        CheckConstraint(
            "match_window_minutes IS NULL OR match_window_minutes >= 0",
            name="ck_shift_patterns_match_window_nonnegative",
        ),
        CheckConstraint(
            "match_before_minutes BETWEEN 0 AND 120",
            name="ck_shift_patterns_match_before_range",
        ),
        CheckConstraint(
            "match_after_minutes BETWEEN 0 AND 120",
            name="ck_shift_patterns_match_after_range",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(100))
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    # Legacy symmetric margin retained for migration compatibility; new matching uses the separate bounds.
    match_window_minutes: Mapped[int | None] = mapped_column(Integer)
    match_before_minutes: Mapped[int | None] = mapped_column(Integer)
    match_after_minutes: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SecurityShiftAssignment(Base):
    __tablename__ = "security_shift_assignments"
    __table_args__ = (
        UniqueConstraint("employee_id", "work_date", name="uq_security_shift_employee_date"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    shift_pattern_id: Mapped[int] = mapped_column(ForeignKey("shift_patterns.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class EmployeeCodeCounter(Base):
    __tablename__ = "employee_code_counters"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_employee_code_counter_singleton"),
        CheckConstraint("last_number >= 0", name="ck_employee_code_counter_nonnegative"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class EmployeeServicePeriod(Base):
    __tablename__ = "employee_service_periods"
    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="ck_employee_service_period_dates",
        ),
        Index(
            "uq_employee_one_open_service_period", "employee_id", unique=True,
            postgresql_where=(__import__("sqlalchemy").text("end_date IS NULL")),
            sqlite_where=(__import__("sqlalchemy").text("end_date IS NULL")),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    start_reason: Mapped[str] = mapped_column(Text, default="Initial employment")
    end_reason: Mapped[str | None] = mapped_column(Text)


class EmployeeChange(Base):
    __tablename__ = "employee_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(80))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    effective_date: Mapped[date] = mapped_column(Date)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    reason: Mapped[str] = mapped_column(Text)


class EmployeeDataChangeRequest(Base):
    __tablename__ = "employee_data_change_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(40))
    requested_value: Mapped[str] = mapped_column(Text)
    request_reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    decision_reason: Mapped[str | None] = mapped_column(Text)
    effective_date: Mapped[date | None] = mapped_column(Date)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(80))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(80), default="system_admin")
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(80))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

@event.listens_for(AuditLog, "before_update")
def prevent_audit_log_update(mapper, connection, target):
    raise ValueError("Audit log records are immutable.")


@event.listens_for(AuditLog, "before_delete")
def prevent_audit_log_deletion(mapper, connection, target):
    raise ValueError("Audit log records cannot be deleted.")


class AdminAccount(Base):
    __tablename__ = "admin_accounts"
    __table_args__ = (
        UniqueConstraint("singleton_key", name="uq_admin_accounts_singleton_key"),
        CheckConstraint("singleton_key = 1", name="ck_admin_accounts_singleton_key"),
        CheckConstraint("active IS TRUE", name="ck_admin_accounts_must_remain_active"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    singleton_key: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    session_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)


@event.listens_for(AdminAccount, "before_delete")
def prevent_admin_deletion(mapper, connection, target):
    raise ValueError("The sole administrator account cannot be deleted.")


class AttendanceImportBatch(Base):
    """Immutable source-file receipt and processing state for monthly attendance imports."""
    __tablename__ = "attendance_import_batches"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('program_report', 'fingerprint')",
            name="ck_attendance_import_source_type",
        ),
        CheckConstraint(
            "status IN ('uploaded', 'parsed', 'needs_review', 'approved', 'rejected')",
            name="ck_attendance_import_status",
        ),
        CheckConstraint("period_end >= period_start", name="ck_attendance_import_period"),
        CheckConstraint("row_count >= 0 AND issue_count >= 0", name="ck_attendance_import_counts"),
        UniqueConstraint("source_type", "file_sha256", name="uq_attendance_import_source_hash"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="uploaded", nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    issue_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(80))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class AttendancePunch(Base):
    """Parsed fingerprint event; raw source identity and cell text are retained for traceability."""
    __tablename__ = "attendance_punches"
    __table_args__ = (
        UniqueConstraint(
            "import_batch_id", "source_sheet", "source_row", "event_index",
            name="uq_attendance_punch_source_position",
        ),
        CheckConstraint("event_index >= 0", name="ck_attendance_punch_event_index"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    import_batch_id: Mapped[int] = mapped_column(
        ForeignKey("attendance_import_batches.id", ondelete="RESTRICT"), index=True
    )
    employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), index=True
    )
    raw_employee_code: Mapped[str | None] = mapped_column(String(80))
    raw_employee_name: Mapped[str | None] = mapped_column(String(200))
    punch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), index=True)
    raw_timestamp: Mapped[str] = mapped_column(String(120), nullable=False)
    event_type: Mapped[str | None] = mapped_column(String(40))
    source_sheet: Mapped[str] = mapped_column(String(120), nullable=False)
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)
    event_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class AttendanceDailyRecord(Base):
    """One employee/work-date reconciliation result, pending HR approval by default."""
    __tablename__ = "attendance_daily_records"
    __table_args__ = (
        UniqueConstraint("employee_id", "work_date", name="uq_attendance_daily_employee_date"),
        CheckConstraint(
            "status IN ('needs_review', 'ready_for_review', 'approved', 'rejected')",
            name="ck_attendance_daily_status",
        ),
        CheckConstraint(
            "scheduled_end_at IS NULL OR scheduled_start_at IS NULL "
            "OR scheduled_end_at > scheduled_start_at",
            name="ck_attendance_daily_schedule_order",
        ),
        CheckConstraint("worked_minutes IS NULL OR worked_minutes >= 0", name="ck_attendance_daily_worked_minutes"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), index=True
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    shift_pattern_id: Mapped[int | None] = mapped_column(ForeignKey("shift_patterns.id"))
    security_shift_assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("security_shift_assignments.id", ondelete="RESTRICT")
    )
    program_import_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("attendance_import_batches.id", ondelete="RESTRICT")
    )
    fingerprint_import_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("attendance_import_batches.id", ondelete="RESTRICT")
    )
    scheduled_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    scheduled_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    program_clock_in: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    program_clock_out: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    first_clock_in: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    last_clock_out: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    worked_minutes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="needs_review", nullable=False)
    review_notes: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(String(80))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
