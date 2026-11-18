"""Create the historical base schema and enforce one active administrator."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _create_table_if_missing(name, *columns, **kwargs):
    if name not in _inspector().get_table_names():
        op.create_table(name, *columns, **kwargs)


def _ensure_admin_guards():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("admin_accounts")}

    if "singleton_key" not in columns:
        admin_count = bind.execute(sa.text("SELECT COUNT(*) FROM admin_accounts")).scalar_one()
        if admin_count > 1:
            raise RuntimeError(
                "Cannot enforce the single-admin rule: admin_accounts already contains "
                "more than one row. Resolve the duplicate accounts before migrating; "
                "no account was changed."
            )
        op.add_column(
            "admin_accounts",
            sa.Column("singleton_key", sa.Integer(), nullable=False, server_default="1"),
        )

    invalid_admin = bind.execute(
        sa.text(
            "SELECT id FROM admin_accounts "
            "WHERE singleton_key <> 1 OR active IS DISTINCT FROM TRUE LIMIT 1"
        )
    ).first()
    if invalid_admin:
        raise RuntimeError(
            "Cannot enforce the single active administrator rule: an existing admin row "
            "has an invalid singleton_key or is inactive. No account data was changed."
        )

    inspector = sa.inspect(bind)
    unique_names = {item.get("name") for item in inspector.get_unique_constraints("admin_accounts")}
    if "uq_admin_accounts_singleton_key" not in unique_names:
        op.create_unique_constraint(
            "uq_admin_accounts_singleton_key", "admin_accounts", ["singleton_key"]
        )

    check_names = {item.get("name") for item in sa.inspect(bind).get_check_constraints("admin_accounts")}
    if "ck_admin_accounts_singleton_key" not in check_names:
        op.create_check_constraint(
            "ck_admin_accounts_singleton_key", "admin_accounts", "singleton_key = 1"
        )
    if "ck_admin_accounts_must_remain_active" not in check_names:
        op.create_check_constraint(
            "ck_admin_accounts_must_remain_active", "admin_accounts", "active IS TRUE"
        )

    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION prevent_admin_account_deletion()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'The sole administrator account cannot be deleted';
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute(
            "DROP TRIGGER IF EXISTS trg_prevent_admin_account_deletion ON admin_accounts"
        )
        op.execute("""
            CREATE TRIGGER trg_prevent_admin_account_deletion
            BEFORE DELETE ON admin_accounts FOR EACH ROW
            EXECUTE FUNCTION prevent_admin_account_deletion()
        """)


def upgrade():
    _create_table_if_missing(
        "departments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("name", name="uq_departments_name"),
    )
    _create_table_if_missing(
        "branches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("department_id", sa.Integer(), sa.ForeignKey("departments.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("department_id", "name", name="uq_branches_department_name"),
    )
    _create_table_if_missing(
        "employees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(20), nullable=False, unique=True),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("national_id", sa.String(20), nullable=True, unique=True),
        sa.Column("phone", sa.String(30), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("fingerprint_id", sa.String(50), nullable=True, unique=True),
        sa.Column("department_id", sa.Integer(), sa.ForeignKey("departments.id"), nullable=False),
        sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branches.id"), nullable=True),
        sa.Column("position", sa.String(120), nullable=False),
        sa.Column("hire_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("basic_salary", sa.Numeric(12, 2), nullable=False),
        sa.Column("daily_hours", sa.Numeric(5, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    _create_table_if_missing(
        "employee_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("field_name", sa.String(80), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reason", sa.Text(), nullable=False),
    )
    _create_table_if_missing(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor", sa.String(80), nullable=False, server_default="system_admin"),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("entity_id", sa.String(80), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    _create_table_if_missing(
        "admin_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("singleton_key", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("username", sa.String(80), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    existing_indexes = {item["name"] for item in _inspector().get_indexes("branches")}
    if "ix_branches_department_id" not in existing_indexes:
        op.create_index("ix_branches_department_id", "branches", ["department_id"])
    existing_indexes = {item["name"] for item in _inspector().get_indexes("employees")}
    for name, column in (
        ("ix_employees_code", "code"),
        ("ix_employees_full_name", "full_name"),
    ):
        if name not in existing_indexes:
            op.create_index(name, "employees", [column])
    existing_indexes = {item["name"] for item in _inspector().get_indexes("employee_changes")}
    if "ix_employee_changes_employee_id" not in existing_indexes:
        op.create_index("ix_employee_changes_employee_id", "employee_changes", ["employee_id"])
    existing_indexes = {item["name"] for item in _inspector().get_indexes("audit_logs")}
    if "ix_audit_logs_action" not in existing_indexes:
        op.create_index("ix_audit_logs_action", "audit_logs", ["action"])

    _ensure_admin_guards()


def downgrade():
    # Deliberately retain the schema and administrator guards: removing columns or
    # constraints here could discard data or weaken the single-admin invariant.
    pass
