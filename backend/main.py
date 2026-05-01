"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.routes import router as api_router
from api.websocket import ws_router
from core.config import get_settings
from core.limiter import limiter
from tools.docker_tools import client_available

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    docker_ok = client_available()
    if docker_ok:
        logger.info("Docker daemon is available")
    else:
        logger.warning(
            "Docker daemon is not available — testing agent runs will fail until Docker starts"
        )
    logger.info(
        "Multi-Agent Orchestration API starting (frontend=%s, port=%s)",
        settings.frontend_url,
        settings.backend_port,
    )
    yield
    logger.info("Multi-Agent Orchestration API shutting down")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Multi-Agent Orchestration",
        description="Autonomous multi-agent pipeline: GitHub issue → PR",
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    cors_origins = list({settings.frontend_url, "http://localhost:5173"})
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    app.include_router(ws_router)

    @app.get("/health")
    async def root_health() -> dict[str, str]:
        """Simple liveness probe (use /api/health for Docker status)."""
        return {"status": "ok"}

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "message": str(exc),
                "run_id": None,
            },
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "main:app",
        host=s.backend_host,
        port=s.backend_port,
        reload=True,
    )
