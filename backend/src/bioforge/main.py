from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from bioforge import __version__
from bioforge.api.agent import router as agent_router
from bioforge.api.auth import router as auth_router
from bioforge.api.benchmarks import router as benchmarks_router
from bioforge.api.files import router as files_router
from bioforge.api.gpu import router as gpu_router
from bioforge.api.pipelines import router as pipelines_router
from bioforge.api.predictions import router as predictions_router
from bioforge.api.projects import router as projects_router
from bioforge.api.usage import router as usage_router
from bioforge.auth.passwords import NON_VERIFIABLE_HASH
from bioforge.constants import DEFAULT_PROJECT_ID, DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from bioforge.db.engine import init_db, session_factory
from bioforge.db.models import Project, User
from bioforge.observability import configure_tracing


async def _bootstrap_default_user() -> None:
    """Idempotent: ensure the default user exists. It owns all legacy/single-user data and is the
    identity every request resolves to when auth is OFF. Created with a non-verifiable password
    hash, so it can never be logged into."""
    async with session_factory() as session:
        existing = await session.get(User, DEFAULT_USER_ID)
        if existing is None:
            session.add(
                User(
                    id=DEFAULT_USER_ID,
                    email=DEFAULT_USER_EMAIL,
                    password_hash=NON_VERIFIABLE_HASH,
                    display_name="Default user",
                )
            )
            await session.commit()


async def _bootstrap_default_project() -> None:
    """Idempotent: create the `default-project` row if it doesn't already exist, owned by the
    default user.

    The hardcoded `DEFAULT_PROJECT_ID` is what every Trace.project_id has pointed at
    since Phase 0. Now that we have a real `projects` table with a foreign-key target
    (from ProjectMemory), the row must actually exist before any trace or memory write
    references it.
    """
    async with session_factory() as session:
        existing = (await session.execute(select(Project).where(Project.id == DEFAULT_PROJECT_ID))).scalar_one_or_none()
        if existing is None:
            session.add(
                Project(
                    id=DEFAULT_PROJECT_ID,
                    name="Default project",
                    description=(
                        "Auto-created on first startup. Holds traces and memory until you create named projects."
                    ),
                    user_id=DEFAULT_USER_ID,
                )
            )
            await session.commit()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # configure_tracing() honors the BIOFORGE_OTEL_ENABLED setting. When disabled,
    # this is a no-op and the agent loop uses the OpenTelemetry no-op tracer (zero
    # runtime cost). When enabled with exporter=console, spans print to stdout.
    configure_tracing()
    await init_db()
    await _bootstrap_default_user()
    await _bootstrap_default_project()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="BioForge",
        version=__version__,
        description="Agentic AI bioinformatics platform — Phase 0",
        lifespan=_lifespan,
    )

    # CORS origins come from settings (BIOFORGE_CORS_ORIGINS, comma-separated). Default is the
    # local Vite/dev ports, so local behavior is unchanged; set it to your deployed origin(s)
    # before exposing the API. "*" disables credentialed requests per the CORS spec, so when "*"
    # is requested we drop allow_credentials to keep the browser from rejecting every response.
    from bioforge.config import settings as _settings

    origins = [o.strip() for o in _settings.cors_origins.split(",") if o.strip()]
    allow_any = origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["http://localhost:5173", "http://localhost:3000"],
        allow_credentials=not allow_any,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router)
    app.include_router(agent_router)
    app.include_router(projects_router)
    app.include_router(files_router)
    app.include_router(pipelines_router)
    app.include_router(predictions_router)
    app.include_router(gpu_router)
    app.include_router(usage_router)
    app.include_router(benchmarks_router)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/config")
    async def config() -> dict:
        """Public client config -- lets the frontend decide whether to show the login screen
        (auth on) or go straight in (single-user). No secrets here."""
        from bioforge.config import settings

        return {
            "version": __version__,
            "auth_enabled": settings.auth_enabled,
            "auth_allow_registration": settings.auth_allow_registration,
        }

    return app


app = create_app()
