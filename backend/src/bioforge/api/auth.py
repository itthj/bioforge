"""Accounts API: register / login / logout / me, plus the `get_current_user` dependency every
isolated endpoint depends on.

Auth is opt-in (`settings.auth_enabled`). When OFF, `get_current_user` returns the bootstrapped
default user, so the rest of the app never special-cases "no user" -- there is always a current
user, it's just the default one. When ON, a valid Bearer token (from POST /auth/login) is required.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bioforge.auth import generate_token, hash_password, hash_token, verify_password
from bioforge.auth.passwords import NON_VERIFIABLE_HASH
from bioforge.config import settings
from bioforge.constants import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from bioforge.db.engine import get_session
from bioforge.db.models import AuthSession, Project, User

router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --- Schemas -------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    display_name: str | None = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str | None


class LoginResponse(BaseModel):
    token: str = Field(description="Opaque bearer token. Send it as 'Authorization: Bearer <token>'.")
    user: UserResponse


def _to_user_response(user: User) -> UserResponse:
    return UserResponse(id=user.id, email=user.email, display_name=user.display_name)


def _normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if not _EMAIL_RE.match(normalized):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    return normalized


# --- Current-user resolution ---------------------------------------------------------


async def _get_or_create_default_user(session: AsyncSession) -> User:
    """The identity used when auth is OFF. Idempotent so it works whether the schema came from a
    migration (which seeds the row) or from create_all (tests)."""
    user = await session.get(User, DEFAULT_USER_ID)
    if user is None:
        user = User(
            id=DEFAULT_USER_ID,
            email=DEFAULT_USER_EMAIL,
            password_hash=NON_VERIFIABLE_HASH,  # not loginable
            display_name="Default user",
        )
        session.add(user)
        await session.flush()
    return user


async def _user_for_token(session: AsyncSession, token: str) -> User | None:
    row = (
        await session.execute(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    ).scalar_one_or_none()
    if row is None or row.revoked:
        return None
    now = datetime.now(UTC)
    expires_at = row.expires_at
    # DateTime(timezone=True) round-trips as a NAIVE datetime on SQLite; treat it as UTC so the
    # comparison below doesn't raise on naive-vs-aware.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < now:
        return None
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    row.last_used_at = now
    return user


async def get_current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> User:
    """FastAPI dependency: the authenticated user, or the default user when auth is disabled."""
    if not settings.auth_enabled:
        return await _get_or_create_default_user(session)
    if authorization is None or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Send 'Authorization: Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[len("bearer ") :].strip()
    user = await _user_for_token(session, token)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired session token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# --- Endpoints -----------------------------------------------------------------------


@router.post("/auth/register", response_model=UserResponse, status_code=201)
async def register(body: RegisterRequest, session: AsyncSession = Depends(get_session)) -> UserResponse:
    if not settings.auth_allow_registration:
        raise HTTPException(status_code=403, detail="Registration is disabled on this instance.")
    email = _normalize_email(body.email)
    if len(body.password) < settings.auth_min_password_length:
        raise HTTPException(
            status_code=422,
            detail=f"Password must be at least {settings.auth_min_password_length} characters.",
        )
    user = User(email=email, password_hash=hash_password(body.password), display_name=body.display_name)
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail="An account with that email already exists.") from e
    return _to_user_response(user)


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)) -> LoginResponse:
    email = body.email.strip().lower()
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    # Always run a verify (against a sentinel when the email is unknown) so response time doesn't
    # reveal which emails are registered.
    candidate_hash = user.password_hash if user is not None else NON_VERIFIABLE_HASH
    ok = verify_password(body.password, candidate_hash) and user is not None and user.is_active
    if not ok:
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    assert user is not None  # ok implies user is not None
    raw_token = generate_token()
    session.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            expires_at=datetime.now(UTC) + timedelta(hours=settings.auth_session_ttl_hours),
        )
    )
    await session.flush()
    return LoginResponse(token=raw_token, user=_to_user_response(user))


@router.post("/auth/logout", status_code=204)
async def logout(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Revoke the presented session token. Idempotent + always 204 (we don't leak whether the token
    was valid)."""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[len("bearer ") :].strip()
        row = (
            await session.execute(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        ).scalar_one_or_none()
        if row is not None:
            row.revoked = True
            await session.flush()


@router.get("/auth/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> UserResponse:
    return _to_user_response(user)


# --- Authorization helpers (used by the project + agent endpoints) -------------------


def owns(project: Project | None, user: User) -> bool:
    """Whether `user` may access `project`. When auth is OFF, ownership isn't enforced (single
    user), so any existing project is accessible -- preserving the pre-auth behavior exactly."""
    if project is None:
        return False
    if not settings.auth_enabled:
        return True
    return project.user_id == user.id


async def require_project_access(session: AsyncSession, project_id: str, user: User) -> None:
    """Raise 404 if `user` can't access `project_id`. No-op when auth is OFF (we don't even require
    the project to exist there -- traces have always carried free-form project_ids). When auth is
    ON, a missing OR other-user project is reported as 404, never 403, so the response can't be used
    to probe which projects exist for other accounts."""
    if not settings.auth_enabled:
        return
    project = await session.get(Project, project_id)
    if not owns(project, user):
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")


async def require_owned_project(session: AsyncSession, project_id: str, user: User) -> Project:
    """Like require_project_access, but the project must EXIST (returns it). Used by file ops, which
    physically store bytes under a project_id -- uploading into a non-existent project would orphan
    them. Existence is enforced regardless of auth; ownership only when auth is on."""
    project = await session.get(Project, project_id)
    if project is None or not owns(project, user):
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")
    return project
