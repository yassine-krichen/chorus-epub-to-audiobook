"""FastAPI app factory for the book2audio control-plane API."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from book2audio.api.auth.routes import router as auth_router
from book2audio.api.jobs.routes import router as jobs_router


def create_app() -> FastAPI:
    app = FastAPI(title="book2audio API")

    origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
    if "*" in origins:
        # Starlette would reflect any Origin back to the client while still
        # allowing credentialed requests — effectively no CORS protection at all.
        raise RuntimeError(
            "CORS_ORIGINS must not contain '*' while allow_credentials is enabled"
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router)
    app.include_router(jobs_router)
    return app


app = create_app()
