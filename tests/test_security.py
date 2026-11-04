import pytest

from app.security import hash_password, verify_password


def test_password_hash_verifies_without_storing_plaintext():
    password = "Hotel-Admin-Strong-2026!"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed)
    assert not verify_password("wrong-password", hashed)


def test_short_password_is_rejected():
    with pytest.raises(ValueError):
        hash_password("short")
