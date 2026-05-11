"""REST API routes for orchestration runs."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from core.config import get_settings
from core.limiter import limiter
from core.run_manager import run_manager
from core.state import AgentState, create_initial_state
from graph.orchestrator import run_graph
from tools.docker_tools import client_available
from tools.github_tools import GitHubToolsError, get_github_tools

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["orchestration"])

ISSUE_URL_PATTERN = re.compile(
    r"^https://github\.com/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+/issues/\d+$"
)


class RunCreateRequest(BaseModel):
    issue_url: str
    github_token: str | None = None
    model_name: str = "deepseek-ai/deepseek-v4-pro"

    @field_validator("issue_url")
    @classmethod
    def validate_issue_url(cls, value: str) -> str:
        if not ISSUE_URL_PATTERN.match(value.strip()):
            raise ValueError(
                "issue_url must match https://github.com/owner/repo/issues/123"
            )
        return value.strip()


class RunCreateResponse(BaseModel):
    run_id: str
    status: str
    websocket_url: str


def _public_state(state: AgentState) -> Dict[str, Any]:
    data = dict(state)
    data.pop("github_token", None)
    return data


def _websocket_url(run_id: str) -> str:
    settings = get_settings()
    host = settings.backend_host
    if host in ("0.0.0.0", "::"):
        host = "localhost"
    return f"ws://{host}:{settings.backend_port}/ws/{run_id}"


async def _background_run(initial_state: AgentState, queue: asyncio.Queue) -> None:
    """Execute the LangGraph pipeline and persist the final state."""
    run_id = initial_state["run_id"]
    try:
        final_state = await run_graph(initial_state, queue)
        if final_state is not None:
            run_manager.update_state(run_id, dict(final_state))
    except Exception as exc:
        logger.exception("Background run failed for %s", run_id)
        run_manager.update_state(
            run_id,
            {
                "final_status": "failed",
                "error_message": str(exc),
            },
        )


@router.post("/runs", response_model=RunCreateResponse)
@limiter.limit("5/minute")
async def create_run(request: Request, body: RunCreateRequest) -> RunCreateResponse:
    """
    Start a new orchestration run for a GitHub issue.

    Validates URL format, verifies GitHub access, and launches the graph in the background.
    """
    run_id = str(uuid.uuid4())

    settings = get_settings()
    token = (body.github_token or "").strip() or settings.github_token.strip()
    if not token:
        raise HTTPException(
            status_code=400,
            detail="GitHub token required. Provide it in the request or set GITHUB_TOKEN.",
        )

    try:
        initial_state = create_initial_state(
            run_id=run_id,
            issue_url=body.issue_url,
            github_token=token,
            model_name=body.model_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    github = get_github_tools(token)
    try:
        await asyncio.to_thread(
            github.get_issue_details,
            initial_state["repo_owner"],
            initial_state["repo_name"],
            initial_state["issue_number"],
        )
    except (GitHubToolsError, Exception) as exc:
        raise HTTPException(
            status_code=400,
            detail="Cannot access GitHub issue. Check the URL and token permissions.",
        ) from exc

    queue = run_manager.create_run(run_id, initial_state)
    asyncio.create_task(_background_run(initial_state, queue))

    return RunCreateResponse(
        run_id=run_id,
        status="started",
        websocket_url=_websocket_url(run_id),
    )


@router.get("/runs/{run_id}")
@limiter.limit("60/minute")
async def get_run(request: Request, run_id: str) -> Dict[str, Any]:
    """Return the current state snapshot for a run (secrets redacted)."""
    state = run_manager.get_state(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return _public_state(state)


@router.get("/health")
async def api_health() -> Dict[str, Any]:
    """Health check with Docker availability."""
    from datetime import datetime, timezone

    return {
        "status": "ok",
        "docker_available": client_available(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
