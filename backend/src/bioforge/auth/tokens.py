"""Opaque session-token generation + hashing.

A login token is a random, opaque string (no structure, nothing to forge). We hand the RAW token to
the client exactly once (the login response) and persist only its SHA-256 in `auth_sessions`. A
request presents the raw token; we hash it and look the session up. So a database dump yields only
hashes -- never a token that can be replayed as a live login.
"""

from __future__ import annotations

import hashlib
import secrets


def generate_token() -> str:
    """A URL-safe opaque bearer token with 256 bits of entropy (~43 chars)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex of the token -- what we store and index on. A fast hash is correct here: the
    token already has full entropy, so there is nothing to brute-force (unlike a password)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
