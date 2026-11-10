"""bcrypt work factor: production default and rehash detection."""

from __future__ import annotations

import pytest

from memchentic.auth import passwords


def test_production_work_factor_is_twelve() -> None:
    assert passwords.DEFAULT_BCRYPT_ROUNDS == 12


def test_hash_uses_current_work_factor() -> None:
    hashed = passwords.hash_password("correct horse battery")
    assert hashed.split("$")[2] == f"{passwords.BCRYPT_ROUNDS:02d}"
    assert passwords.verify_password("correct horse battery", hashed)
    assert not passwords.password_needs_rehash(hashed)


def test_needs_rehash_when_cost_differs(monkeypatch: pytest.MonkeyPatch) -> None:
    hashed = passwords.hash_password("correct horse battery")
    monkeypatch.setattr(passwords, "BCRYPT_ROUNDS", passwords.BCRYPT_ROUNDS + 1)
    assert passwords.password_needs_rehash(hashed)


@pytest.mark.parametrize("value", ["", "not-a-hash", "$2b$xx$abc"])
def test_needs_rehash_on_malformed_hash(value: str) -> None:
    assert passwords.password_needs_rehash(value)
