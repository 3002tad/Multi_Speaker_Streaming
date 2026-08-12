"""FastAPI application factory isolated from the AI runtime."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def create_application() -> FastAPI:
    """Create the transport adapter without loading ASR models.

    Routes remain registered by :mod:`meeting_ai.main` during the incremental
    extraction.  Keeping construction here makes the API boundary explicit
    and lets later tests instantiate the adapter independently of workers.
    """
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app
