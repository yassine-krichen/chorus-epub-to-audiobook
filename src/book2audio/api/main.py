"""FastAPI app factory for the book2audio control-plane API."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from book2audio.api.auth.routes import router as auth_router
from book2audio.api.jobs import service as jobs_service
from book2audio.api.jobs.routes import router as jobs_router
from book2audio.api.voices.routes import router as voices_router


def create_app() -> FastAPI:
    app = FastAPI(title="book2audio API")

    origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
    if "*" in origins:
        # Starlette would reflect any Origin back to the client while still
        # allowing credentialed requests — effectively no CORS protection at all.
        raise RuntimeError(
            "CORS_ORIGINS must not contain '*' while allow_credentials is enabled"
        )
    # Fail fast on a bad WORKER_MODE at startup, not on first job creation.
    jobs_service.worker_mode()
    # Same for the flag that lets hosted jobs spend the LLM key.
    jobs_service.hosted_llm_attribution_enabled()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router)
    app.include_router(jobs_router)
    app.include_router(voices_router)
    return app


app = create_app()
