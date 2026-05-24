from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from bioforge import __version__
from bioforge.api.agent import router as agent_router
from bioforge.db.engine import init_db


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await init_db()
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

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
