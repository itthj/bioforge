"""Accounts layer (Phase 6, slice 1): password/token primitives + register/login/logout/me.

Auth is opt-in. These tests exercise both modes:
  - OFF (default): /auth/me returns the bootstrapped default user; existing endpoints are untouched.
  - ON: register -> login -> Bearer token -> /auth/me; bad/expired/revoked tokens are rejected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bioforge.auth import generate_token, hash_password, hash_token, verify_password
from bioforge.auth.passwords import NON_VERIFIABLE_HASH
from bioforge.config import settings
from bioforge.constants import DEFAULT_USER_ID
from bioforge.db.models import AuthSession, User
from sqlalchemy import select

# --- Unit: password hashing ----------------------------------------------------------


def test_password_hash_round_trips() -> None:
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"  # never stored in the clear
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong password", h)


def test_sentinel_hash_never_verifies() -> None:
    """The default user's non-verifiable hash can't be logged into."""
    assert not verify_password("", NON_VERIFIABLE_HASH)
    assert not verify_password("anything", NON_VERIFIABLE_HASH)


def test_hashes_are_salted_unique() -> None:
    assert hash_password("same") != hash_password("same")  # per-hash salt


# --- Unit: tokens --------------------------------------------------------------------


def test_tokens_are_unique_and_hash_is_stable() -> None:
    a, b = generate_token(), generate_token()
    assert a != b
    assert len(a) > 20
    assert hash_token(a) == hash_token(a)  # deterministic
    assert hash_token(a) != hash_token(b)
    assert len(hash_token(a)) == 64  # sha256 hex


# --- auth OFF (default) --------------------------------------------------------------


async def test_me_returns_default_user_when_auth_disabled(streaming_client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", False)
    resp = await streaming_client.get("/auth/me")
    assert resp.status_code == 200
    assert resp.json()["id"] == DEFAULT_USER_ID


# --- auth ON: register / login / me --------------------------------------------------


async def test_register_login_me_flow(streaming_client, test_session_maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)

    reg = await streaming_client.post(
        "/auth/register",
        json={"email": "Ada@Example.com", "password": "a-strong-password", "display_name": "Ada"},
    )
    assert reg.status_code == 201
    assert reg.json()["email"] == "ada@example.com"  # normalized lower-case

    login = await streaming_client.post(
        "/auth/login", json={"email": "ada@example.com", "password": "a-strong-password"}
    )
    assert login.status_code == 200
    token = login.json()["token"]
    assert token

    # The token is stored only as a hash -- never in the clear.
    async with test_session_maker() as s:
        rows = (await s.execute(select(AuthSession))).scalars().all()
        assert len(rows) == 1
        assert rows[0].token_hash == hash_token(token)
        assert rows[0].token_hash != token

    me = await streaming_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "ada@example.com"


async def test_protected_me_requires_a_token_when_auth_on(streaming_client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    assert (await streaming_client.get("/auth/me")).status_code == 401
    bad = await streaming_client.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert bad.status_code == 401


async def test_login_rejects_bad_credentials(streaming_client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    await streaming_client.post("/auth/register", json={"email": "x@y.com", "password": "right-password-1"})
    assert (
        await streaming_client.post("/auth/login", json={"email": "x@y.com", "password": "wrong"})
    ).status_code == 401
    # Unknown email is also 401 (not 404 -- don't reveal which emails exist).
    miss = await streaming_client.post("/auth/login", json={"email": "nobody@y.com", "password": "whatever-123"})
    assert miss.status_code == 401


async def test_register_rejects_duplicate_email_weak_password_bad_email(streaming_client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    await streaming_client.post("/auth/register", json={"email": "dup@y.com", "password": "first-strong-pw"})
    dup = await streaming_client.post("/auth/register", json={"email": "dup@y.com", "password": "another-strong-pw"})
    assert dup.status_code == 409
    weak = await streaming_client.post("/auth/register", json={"email": "new@y.com", "password": "short"})
    assert weak.status_code == 422
    bad_email = await streaming_client.post(
        "/auth/register", json={"email": "not-an-email", "password": "long-enough-pw"}
    )
    assert bad_email.status_code == 422


async def test_registration_can_be_disabled(streaming_client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_allow_registration", False)
    resp = await streaming_client.post("/auth/register", json={"email": "late@y.com", "password": "long-enough-pw"})
    assert resp.status_code == 403


# --- logout + expiry -----------------------------------------------------------------


async def test_logout_revokes_the_token(streaming_client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    await streaming_client.post("/auth/register", json={"email": "bob@y.com", "password": "bobs-strong-pw"})
    token = (
        await streaming_client.post("/auth/login", json={"email": "bob@y.com", "password": "bobs-strong-pw"})
    ).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}

    assert (await streaming_client.get("/auth/me", headers=auth)).status_code == 200
    assert (await streaming_client.post("/auth/logout", headers=auth)).status_code == 204
    # Token is dead after logout.
    assert (await streaming_client.get("/auth/me", headers=auth)).status_code == 401


async def test_expired_session_is_rejected(streaming_client, test_session_maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    raw = generate_token()
    async with test_session_maker() as s:
        s.add(User(id="u-exp", email="exp@y.com", password_hash=hash_password("x-strong-pw-1"), display_name=None))
        s.add(
            AuthSession(
                user_id="u-exp",
                token_hash=hash_token(raw),
                expires_at=datetime.now(UTC) - timedelta(hours=1),  # already expired
            )
        )
        await s.commit()

    resp = await streaming_client.get("/auth/me", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 401
