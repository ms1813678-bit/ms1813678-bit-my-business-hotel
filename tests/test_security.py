import pytest

from app.security import hash_password, verify_password


TEST_PASSWORD = "Test-Only-Password-123!"
WRONG_TEST_PASSWORD = "Wrong-Test-Password-456!"


def test_password_hash_verifies_without_storing_plaintext():
    hashed = hash_password(TEST_PASSWORD)
    assert hashed != TEST_PASSWORD
    assert verify_password(TEST_PASSWORD, hashed)
    assert not verify_password(WRONG_TEST_PASSWORD, hashed)


def test_short_password_is_rejected():
    with pytest.raises(ValueError):
        hash_password("short")


def test_non_string_password_is_rejected():
    with pytest.raises(TypeError):
        hash_password(None)


def test_malformed_hash_fails_closed():
    assert not verify_password(TEST_PASSWORD, "not-a-valid-password-hash")
    assert not verify_password(TEST_PASSWORD, "")
    assert not verify_password(None, "not-a-valid-password-hash")
