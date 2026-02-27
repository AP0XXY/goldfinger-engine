"""Goldfinger — FastAPI application factory.

Supports two modes:
  1. Standalone (single-user): Dashboard + API, reads from .env
  2. Service (multi-user): API only, receives encrypted creds per-request
     Protected by ENGINE_API_KEY header auth.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .routes.api import router as api_router
from .background import background_scanner
from ..data.database import init_db

logger = logging.getLogger(__name__)

# Resolve paths relative to the project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_STATIC_DIR = _PROJECT_ROOT / "src" / "static"
_TEMPLATES_DIR = _PROJECT_ROOT / "src" / "templates"


def create_app() -> FastAPI:
    """Build and configure the FastAPI app."""
    app = FastAPI(
        title="Goldfinger Engine",
        docs_url=None,   # Hide Swagger — Fort Knox
        redoc_url=None,   # Hide ReDoc — Fort Knox
        openapi_url=None, # Hide OpenAPI schema — Fort Knox
    )

    # ── CORS (allow Next.js frontend) ──────────────────────────
    allowed_origins = os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:3000,https://goldfinger.vercel.app"
    ).split(",")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    # ── ENGINE_API_KEY middleware ───────────────────────────────
    engine_key = os.environ.get("ENGINE_API_KEY", "")

    @app.middleware("http")
    async def verify_engine_key(request: Request, call_next):
        """Verify ENGINE_API_KEY on API routes (multi-user mode).

        Skip auth for:
        - Local dashboard routes (/, /static, etc.)
        - Health check (/api/health)
        - When ENGINE_API_KEY is not set (standalone mode)
        """
        path = request.url.path

        # Skip for non-API routes and health check
        if not path.startswith("/api") or path == "/api/health":
            return await call_next(request)

        # If ENGINE_API_KEY is set, enforce it
        if engine_key:
            provided = request.headers.get("X-Engine-Key", "")
            if provided != engine_key:
                return JSONResponse(
                    status_code=403,
                    content={"error": "Forbidden — invalid ENGINE_API_KEY"},
                )

        return await call_next(request)

    # ── Static files + Templates (standalone mode) ─────────────
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    if _TEMPLATES_DIR.exists():
        app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
        # Only include dashboard routes if templates exist (standalone)
        from .routes.dashboard import router as dashboard_router
        app.include_router(dashboard_router)

    # ── API routes ─────────────────────────────────────────────
    app.include_router(api_router, prefix="/api")

    # ── Background scanner ───────────────────────────────────────
    scanner_task = None
    
    @app.on_event("startup")
    async def startup_event():
        """Initialize database and start background scanner."""
        nonlocal scanner_task
        init_db()
        logger.info("Database initialized")
        
        # Start background scanner
        scanner_task = asyncio.create_task(background_scanner())
        logger.info("Background scanner started")
    
    @app.on_event("shutdown")
    async def shutdown_event():
        """Stop background scanner on shutdown."""
        nonlocal scanner_task
        if scanner_task:
            scanner_task.cancel()
            try:
                await scanner_task
            except asyncio.CancelledError:
                pass
        logger.info("Background scanner stopped")

    return app


app = create_app()
