"""Create the schema and enforce a single active administrator."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from app import models  # noqa: F401
from app.db import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)
    inspector = inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("admin_accounts")}
    if "singleton_key" not in columns:
        op.add_column("admin_accounts", sa.Column("singleton_key", sa.Integer(), nullable=False, server_default="1"))
    inspector = inspect(bind)
    unique_names = {c.get("name") for c in inspector.get_unique_constraints("admin_accounts")}
    if "uq_admin_accounts_singleton_key" not in unique_names:
        op.create_unique_constraint("uq_admin_accounts_singleton_key", "admin_accounts", ["singleton_key"])
    check_names = {c.get("name") for c in inspector.get_check_constraints("admin_accounts")}
    if "ck_admin_accounts_singleton_key" not in check_names:
        op.create_check_constraint("ck_admin_accounts_singleton_key", "admin_accounts", "singleton_key = 1")
    if "ck_admin_accounts_must_remain_active" not in check_names:
        op.create_check_constraint("ck_admin_accounts_must_remain_active", "admin_accounts", "active IS TRUE")
    if bind.dialect.name == "postgresql":
        op.execute("""
        CREATE OR REPLACE FUNCTION prevent_admin_account_deletion()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'The sole administrator account cannot be deleted';
        END;
        $$ LANGUAGE plpgsql
        """)
        op.execute("""
        DROP TRIGGER IF EXISTS trg_prevent_admin_account_deletion ON admin_accounts;
        CREATE TRIGGER trg_prevent_admin_account_deletion
        BEFORE DELETE ON admin_accounts FOR EACH ROW
        EXECUTE FUNCTION prevent_admin_account_deletion()
        """)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_prevent_admin_account_deletion ON admin_accounts")
        op.execute("DROP FUNCTION IF EXISTS prevent_admin_account_deletion()")
    op.drop_constraint("ck_admin_accounts_must_remain_active", "admin_accounts", type_="check")
    op.drop_constraint("ck_admin_accounts_singleton_key", "admin_accounts", type_="check")
    op.drop_constraint("uq_admin_accounts_singleton_key", "admin_accounts", type_="unique")
    op.drop_column("admin_accounts", "singleton_key")
