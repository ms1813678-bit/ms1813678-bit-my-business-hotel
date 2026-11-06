from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, event
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
