"""Password hashing and verification helpers."""

from pwdlib import PasswordHash

_password_hash = PasswordHash.recommended()
_MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    """Hash a password using the recommended configured password hasher."""
    if not isinstance(password, str):
        raise TypeError("كلمة المرور يجب أن تكون نصًا.")
    if len(password) < _MIN_PASSWORD_LENGTH:
        raise ValueError("كلمة المرور يجب ألا تقل عن 12 حرفًا.")
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password safely; invalid input or a malformed hash fails closed."""
    if not isinstance(password, str) or not isinstance(password_hash, str):
        return False
    if not password or not password_hash:
        return False
    try:
        return _password_hash.verify(password, password_hash)
    except Exception:
        # A malformed or unsupported stored hash must not break authentication.
        return False
