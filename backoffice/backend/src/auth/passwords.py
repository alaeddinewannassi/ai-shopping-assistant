"""Argon2 password hashing (T502).

A thin wrapper so call sites never touch the argon2-cffi API directly — makes it possible
to swap parameters/algorithm later without touching anything but this file.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(plaintext: str) -> str:
    return _hasher.hash(plaintext)


def verify_password(plaintext: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, plaintext)
        return True
    except VerifyMismatchError:
        return False
