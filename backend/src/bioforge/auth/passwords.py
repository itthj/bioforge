"""Password hashing for the accounts layer (argon2id -- the current OWASP-recommended KDF).

We never store or compare raw passwords. `hash_password` produces an argon2id hash at registration;
`verify_password` checks a candidate against a stored hash in constant time and returns a plain bool
(it swallows argon2's exceptions -- a malformed/sentinel hash simply never verifies).
"""

from __future__ import annotations

from argon2 import PasswordHasher

# Stored as the password_hash of the non-loginable default user: it is not a valid argon2 hash, so
# verify_password() against it always returns False -- the default identity can never be logged into.
NON_VERIFIABLE_HASH = "!"

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except Exception:  # noqa: BLE001 -- any argon2 error (mismatch, malformed/sentinel hash) = not verified
        return False
