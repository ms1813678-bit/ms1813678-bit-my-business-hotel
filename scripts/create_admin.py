"""Create the single administrator account interactively; no default credentials."""
from getpass import getpass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import AdminAccount, AuditLog
from app.security import hash_password


def create_admin(username: str, password: str, db=None) -> AdminAccount:
    """Create the one administrator account. Refuse all subsequent setup attempts."""
    if db is None:
        with SessionLocal() as session:
            return create_admin(username, password, session)

    username = username.strip()
    if not username or len(username) > 80:
        raise ValueError("اسم المستخدم مطلوب ويجب ألا يزيد عن 80 حرفًا.")
    password_hash = hash_password(password)
    if db.scalar(select(AdminAccount.id).limit(1)) is not None:
        raise RuntimeError("يوجد حساب مدير بالفعل؛ لا يمكن إنشاء حساب آخر.")

    admin = AdminAccount(username=username, password_hash=password_hash, active=True)
    db.add(admin)
    try:
        db.flush()
        db.add(AuditLog(
            actor=username,
            action="administrator_created",
            entity_type="admin_account",
            entity_id=str(admin.id),
            reason="Initial administrator setup",
        ))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RuntimeError("تعذر إنشاء الحساب؛ قد يكون حساب المدير قد أُنشئ بالفعل.") from exc
    return admin


def main() -> None:
    username = input("اسم مستخدم مسؤول النظام: ").strip()
    password = getpass("كلمة المرور (12 حرفًا على الأقل): ")
    confirmation = getpass("أعد كتابة كلمة المرور: ")
    if password != confirmation:
        raise SystemExit("كلمتا المرور غير متطابقتين.")
    try:
        admin = create_admin(username, password)
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"تم إعداد حساب مسؤول النظام: {admin.username}")


if __name__ == "__main__":
    main()
