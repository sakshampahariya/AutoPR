"""Testing Agent — validates patches inside a Docker sandbox."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from langchain_core.runnables import RunnableConfig

from core.run_manager import emit_event
from core.state import AgentLog, AgentState, TestResult
from tools.docker_tools import DockerSandbox

logger = logging.getLogger(__name__)

_FAILED_LINE = re.compile(r"^(?:FAILED|ERROR)\s+", re.MULTILINE)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_log(message: str, level: str = "info") -> AgentLog:
    return AgentLog(
        agent_name="testing",
        level=level,  # type: ignore[typeddict-item]
        message=message,
        timestamp=_utc_now_iso(),
    )


class TestingAgent:
    """Prepares a test workspace and runs pytest in Docker."""

    def __init__(self, docker_sandbox: DockerSandbox, workspace_dir: str) -> None:
        self._sandbox = docker_sandbox
        self._workspace_dir = workspace_dir

    async def _emit(
        self,
        run_id: str,
        event_queue: Optional[asyncio.Queue],
        event_type: str,
        **kwargs: Any,
    ) -> None:
        await emit_event(run_id, event_type, **kwargs)
        if event_queue is None:
            return
        from core.run_manager import run_manager

        managed = run_manager.get_queue(run_id)
        if managed is event_queue:
            return
        event: Dict[str, Any] = {"type": event_type, "run_id": run_id, **kwargs}
        if event_type == "agent_log":
            if "timestamp" not in event:
                event["timestamp"] = _utc_now_iso()
            if "agent" not in event and "agent_name" in kwargs:
                event["agent"] = kwargs["agent_name"]
        await event_queue.put(event)

    @staticmethod
    def _failed_test_lines(stdout: str) -> List[str]:
        lines: List[str] = []
        for line in stdout.splitlines():
            stripped = line.strip()
            if _FAILED_LINE.match(stripped) or " FAILED " in stripped:
                lines.append(stripped)
        return lines[:20]

    async def run(
        self,
        state: AgentState,
        event_queue: Optional[asyncio.Queue] = None,
    ) -> dict:
        """
        Run tests in Docker and return a partial state update.

        Returns:
            Dict with ``test_result``, ``retry_count``, ``current_node``, ``logs``.
        """
        run_id = state["run_id"]
        logs: List[AgentLog] = []
        file_changes = state.get("file_changes")

        if not file_changes:
            raise ValueError("No file_changes in state; cannot run tests")

        await self._emit(run_id, event_queue, "node_start", node="testing")

        await self._emit(
            run_id,
            event_queue,
            "agent_log",
            agent_name="testing",
            level="info",
            message="Preparing test workspace...",
        )
        logs.append(_make_log("Preparing test workspace..."))

        workspace_path = await asyncio.get_event_loop().run_in_executor(
            None,
            self._sandbox.prepare_workspace,
            file_changes,
            self._workspace_dir,
        )


        await self._emit(
            run_id,
            event_queue,
            "agent_log",
            agent_name="testing",
            level="info",
            message="Spinning up Docker sandbox...",
        )
        logs.append(_make_log("Spinning up Docker sandbox..."))

        loop = asyncio.get_event_loop()
        test_result: TestResult = await loop.run_in_executor(
            None,
            self._sandbox.run_tests,
            workspace_path,
        )

        await self._emit(
            run_id,
            event_queue,
            "test_result",
            result=test_result,
        )

        summary = (
            f"Tests: {test_result['passed']} passed, {test_result['failed']} failed, "
            f"{test_result['errors']} errors — status={test_result['status']}"
        )
        await self._emit(
            run_id,
            event_queue,
            "agent_log",
            agent_name="testing",
            level="info" if test_result["status"] == "pass" else "warning",
            message=summary,
        )
        logs.append(_make_log(summary, level="info" if test_result["status"] == "pass" else "warning"))

        for fail_line in self._failed_test_lines(test_result["stdout"]):
            await self._emit(
                run_id,
                event_queue,
                "agent_log",
                agent_name="testing",
                level="error",
                message=fail_line,
            )
            logs.append(_make_log(fail_line, level="error"))

        await self._emit(run_id, event_queue, "node_complete", node="testing")
        logs.append(_make_log("Testing phase complete."))

        retry_count = state.get("retry_count", 0)
        if test_result["status"] != "pass":
            retry_count += 1

        return {
            "test_result": test_result,
            "retry_count": retry_count,
            "current_node": "testing",
            "logs": logs,
        }


async def testing_node(state: AgentState, config: RunnableConfig) -> dict:
    """LangGraph node function for the Testing Agent."""
    configurable = config.get("configurable") or {}
    event_queue: Optional[asyncio.Queue] = configurable.get("event_queue")
    workspace_dir = configurable.get("workspace_dir")

    if not workspace_dir:
        msg = "workspace_dir missing from config; run Coding Agent first"
        await emit_event(
            state["run_id"],
            "agent_log",
            agent_name="testing",
            level="error",
            message=msg,
        )
        return {
            "final_status": "failed",
            "error_message": msg,
            "current_node": "testing",
            "logs": [
                AgentLog(
                    agent_name="testing",
                    level="error",
                    message=msg,
                    timestamp=_utc_now_iso(),
                )
            ],
        }

    try:
        sandbox = DockerSandbox()
    except RuntimeError as exc:
        await emit_event(
            state["run_id"],
            "agent_log",
            agent_name="testing",
            level="error",
            message=str(exc),
        )
        return {
            "final_status": "failed",
            "error_message": str(exc),
            "current_node": "testing",
            "logs": [
                AgentLog(
                    agent_name="testing",
                    level="error",
                    message=str(exc),
                    timestamp=_utc_now_iso(),
                )
            ],
        }

    agent = TestingAgent(sandbox, str(workspace_dir))

    try:
        return await agent.run(state, event_queue)
    except Exception as exc:
        logger.exception("Testing agent failed for run %s", state.get("run_id"))
        await emit_event(
            state["run_id"],
            "agent_log",
            agent_name="testing",
            level="error",
            message=f"Testing agent failed: {exc}",
        )
        return {
            "final_status": "failed",
            "error_message": f"Testing agent error: {exc}",
            "current_node": "testing",
            "retry_count": state.get("retry_count", 0) + 1,
            "logs": [
                AgentLog(
                    agent_name="testing",
                    level="error",
                    message=str(exc),
                    timestamp=_utc_now_iso(),
                )
            ],
        }
