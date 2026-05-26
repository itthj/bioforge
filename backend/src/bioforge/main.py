from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from bioforge import __version__
from bioforge.api.agent import router as agent_router
from bioforge.api.projects import router as projects_router
from bioforge.constants import DEFAULT_PROJECT_ID
from bioforge.db.engine import init_db, session_factory
from bioforge.db.models import Project
from bioforge.observability import configure_tracing


async def _bootstrap_default_project() -> None:
    """Idempotent: create the `default-project` row if it doesn't already exist.

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
    await _bootstrap_default_project()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="BioForge",
        version=__version__,
        description="Agentic AI bioinformatics platform — Phase 0",
        lifespan=_lifespan,
    )

    # Permissive CORS for local frontend dev. Tighten before any non-local deploy.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(agent_router)
    app.include_router(projects_router)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
