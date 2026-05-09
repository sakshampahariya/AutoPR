"""Run-scoped event queues, state snapshots, and WebSocket event emission."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.state import AgentState


class RunManager:
    """In-memory registry of active orchestration runs."""

    def __init__(self) -> None:
        self._runs: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _sanitize_state(state: AgentState) -> AgentState:
        sanitized: AgentState = dict(state)  # type: ignore[typeddict-item]
        sanitized["github_token"] = "[REDACTED]"
        return sanitized

    def create_run(self, run_id: str, initial_state: AgentState) -> asyncio.Queue:
        """Register a run and return its bounded event queue (max 1000 events)."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._runs[run_id] = {
            "queue": queue,
            "status": initial_state["final_status"],
            "state": self._sanitize_state(initial_state),
            "connected": True,
        }
        return queue

    def get_queue(self, run_id: str) -> Optional[asyncio.Queue]:
        run = self._runs.get(run_id)
        return run["queue"] if run else None

    def update_state(self, run_id: str, state_update: Dict[str, Any]) -> None:
        """Shallow-merge a partial state update into the stored run state."""
        run = self._runs.get(run_id)
        if not run:
            return
        state: AgentState = run["state"]
        merged: AgentState = {**state, **state_update}  # type: ignore[typeddict-item]
        run["state"] = self._sanitize_state(merged)
        if "final_status" in state_update:
            run["status"] = state_update["final_status"]

    def get_state(self, run_id: str) -> Optional[AgentState]:
        run = self._runs.get(run_id)
        return run["state"] if run else None

    def mark_disconnected(self, run_id: str) -> None:
        run = self._runs.get(run_id)
        if run:
            run["connected"] = False

    def mark_connected(self, run_id: str) -> None:
        run = self._runs.get(run_id)
        if run:
            run["connected"] = True

    def is_connected(self, run_id: str) -> bool:
        run = self._runs.get(run_id)
        return bool(run and run["connected"])

    def cleanup_run(self, run_id: str) -> None:
        self._runs.pop(run_id, None)


run_manager = RunManager()


async def emit_event(run_id: str, event_type: str, **kwargs: Any) -> None:
    """
    Enqueue a WebSocket event for the given run.

    For ``agent_log`` events, ensures ``timestamp`` is set and maps
    ``agent_name`` → ``agent`` for the wire format expected by the frontend.
    """
    queue = run_manager.get_queue(run_id)
    if queue is None:
        return

    event: Dict[str, Any] = {"type": event_type, "run_id": run_id, **kwargs}

    if event_type == "agent_log":
        if "timestamp" not in event:
            event["timestamp"] = datetime.now(timezone.utc).isoformat()
        if "agent" not in event and "agent_name" in kwargs:
            event["agent"] = kwargs["agent_name"]

    await queue.put(event)
