"""PR Agent — publishes passing fixes to GitHub as a pull request."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from core.config import get_settings
from core.run_manager import emit_event
from core.state import AgentLog, AgentState, FileChange
from tools.github_tools import GitHubTools, get_github_tools

logger = logging.getLogger(__name__)

PR_SYSTEM_PROMPT = """You are a GitHub automation agent. Your task is to create a well-
structured Pull Request description for a code fix that has already passed all tests.

Generate a PR description in this format:
  ## Summary
  [One paragraph describing what was fixed and why]

  ## Changes Made
  - [File]: [What changed]

  ## Test Evidence
  - Tests run: {total_tests}
  - Tests passed: {passed}
  - Test command: `pytest tests/`

  ## Related Issue
  Closes #{issue_number}

Return only the markdown body (no JSON, no outer fences)."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_log(message: str, level: str = "info") -> AgentLog:
    return AgentLog(
        agent_name="pr",
        level=level,  # type: ignore[typeddict-item]
        message=message,
        timestamp=_utc_now_iso(),
    )


def _message_content_to_str(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts)
    return str(content)


def _file_changes_for_commit(file_changes: List[FileChange]) -> List[dict]:
    return [
        {
            "file_path": change["file_path"],
            "patched_content": change["patched_content"],
        }
        for change in file_changes
    ]


def _fallback_pr_body(state: AgentState) -> str:
    """Build a PR description without the LLM if generation fails."""
    issue_number = state["issue_number"]
    patch_summary = state.get("patch_summary") or "Automated fix from orchestration run."
    test_result = state.get("test_result") or {}
    file_changes = state.get("file_changes") or []

    lines = [
        "## Summary",
        patch_summary,
        "",
        "## Changes Made",
    ]
    for change in file_changes:
        desc = change.get("change_description") or "Updated file"
        lines.append(f"- `{change['file_path']}`: {desc}")

    lines.extend(
        [
            "",
            "## Test Evidence",
            f"- Tests run: {test_result.get('total_tests', 0)}",
            f"- Tests passed: {test_result.get('passed', 0)}",
            "- Test command: `pytest tests/`",
            "",
            "## Related Issue",
            f"Closes #{issue_number}",
        ]
    )
    return "\n".join(lines)


class PRAgent:
    """Creates a branch, commits fixes, and opens a pull request on GitHub."""

    def __init__(self, model_name: str, github_tools: GitHubTools) -> None:
        self._model_name = model_name
        self._github = github_tools
        settings = get_settings()
        model_lower = model_name.lower()

        if "claude" in model_lower:
            from langchain_anthropic import ChatAnthropic

            self._llm = ChatAnthropic(
                model=model_name,
                api_key=settings.anthropic_api_key or None,
                temperature=0,
            )
        elif "gemini" in model_lower:
            from langchain_google_genai import ChatGoogleGenerativeAI

            self._llm = ChatGoogleGenerativeAI(
                model=model_name,
                api_key=settings.gemini_api_key or None,
                temperature=0,
            )
        elif "deepseek" in model_lower:
            from langchain_openai import ChatOpenAI

            self._llm = ChatOpenAI(
                model=model_name,
                api_key=settings.deepseek_api_key or None,
                base_url=settings.deepseek_base_url or "https://api.deepseek.com",
                temperature=0,
            )
        else:
            from langchain_openai import ChatOpenAI

            self._llm = ChatOpenAI(
                model=model_name,
                api_key=settings.openai_api_key or None,
                temperature=0,
            )

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

    async def _generate_pr_description(self, state: AgentState) -> str:
        """
        Generate PR body markdown via LLM using issue, patch, test, and file data.

        Falls back to a deterministic template if the LLM call fails.
        """
        issue_number = state["issue_number"]
        patch_summary = state.get("patch_summary") or ""
        test_result = state.get("test_result") or {}
        file_changes = state.get("file_changes") or []

        changes_text = "\n".join(
            f"- {c['file_path']}: {c.get('change_description', 'modified')}"
            for c in file_changes
        )
        human_content = (
            f"Issue number: {issue_number}\n"
            f"Patch summary: {patch_summary}\n\n"
            f"Test results:\n"
            f"  total_tests: {test_result.get('total_tests', 0)}\n"
            f"  passed: {test_result.get('passed', 0)}\n"
            f"  failed: {test_result.get('failed', 0)}\n"
            f"  status: {test_result.get('status', 'unknown')}\n\n"
            f"File changes:\n{changes_text or '(none listed)'}"
        )

        try:
            response = await self._llm.ainvoke(
                [
                    SystemMessage(content=PR_SYSTEM_PROMPT),
                    HumanMessage(content=human_content),
                ]
            )
            body = _message_content_to_str(response.content).strip()
            if body:
                return body
        except Exception as exc:
            logger.warning("LLM PR description failed, using fallback: %s", exc)

        return _fallback_pr_body(state)

    async def run(
        self,
        state: AgentState,
        event_queue: Optional[asyncio.Queue] = None,
    ) -> dict:
        """
        Create branch, commit patches, and open a pull request.

        Returns:
            Partial state with branch/PR fields and ``final_status: success``.
        """
        run_id = state["run_id"]
        owner = state["repo_owner"]
        repo = state["repo_name"]
        issue_number = state["issue_number"]
        logs: List[AgentLog] = []

        file_changes = state.get("file_changes")
        if not file_changes:
            raise ValueError("No file_changes in state; cannot open PR")

        patch_summary = state.get("patch_summary") or "Automated fix"
        await self._emit(run_id, event_queue, "node_start", node="pr")

        branch_name = f"auto-fix/issue-{issue_number}-{run_id[:8]}"
        await self._emit(
            run_id,
            event_queue,
            "agent_log",
            agent_name="pr",
            level="info",
            message=f"Creating branch {branch_name}...",
        )
        logs.append(_make_log(f"Creating branch {branch_name}..."))

        branch_name = await asyncio.to_thread(
            self._github.create_branch,
            owner,
            repo,
            branch_name,
        )

        await self._emit(
            run_id,
            event_queue,
            "agent_log",
            agent_name="pr",
            level="info",
            message=f"Committing {len(file_changes)} file(s)...",
        )
        logs.append(_make_log(f"Committing {len(file_changes)} file(s)..."))

        commit_message = f"fix: resolve issue #{issue_number} - {patch_summary[:60]}"
        commit_payload = _file_changes_for_commit(file_changes)

        await asyncio.to_thread(
            self._github.commit_files,
            owner,
            repo,
            branch_name,
            commit_payload,
            commit_message,
        )

        await self._emit(
            run_id,
            event_queue,
            "agent_log",
            agent_name="pr",
            level="info",
            message="Generating PR description...",
        )
        logs.append(_make_log("Generating PR description..."))

        pr_body = await self._generate_pr_description(state)
        pr_title = f"fix: resolve issue #{issue_number} - {patch_summary[:60]}"

        pr_result = await asyncio.to_thread(
            self._github.open_pull_request,
            owner,
            repo,
            pr_title,
            pr_body,
            branch_name,
        )

        pr_url = pr_result["pr_url"]
        pr_number = pr_result["pr_number"]

        await self._emit(
            run_id,
            event_queue,
            "pr_created",
            pr_url=pr_url,
            pr_number=pr_number,
        )
        await self._emit(
            run_id,
            event_queue,
            "agent_log",
            agent_name="pr",
            level="info",
            message=f"Pull request opened: {pr_url}",
        )
        logs.append(_make_log(f"Pull request opened: {pr_url}"))

        await self._emit(run_id, event_queue, "node_complete", node="pr")
        logs.append(_make_log("PR phase complete."))

        return {
            "branch_name": branch_name,
            "pr_url": pr_url,
            "pr_number": pr_number,
            "final_status": "success",
            "current_node": "pr",
            "logs": logs,
        }


async def pr_node(state: AgentState, config: RunnableConfig) -> dict:
    """LangGraph node function for the PR Agent."""
    configurable = config.get("configurable") or {}
    event_queue: Optional[asyncio.Queue] = configurable.get("event_queue")
    github_tools = get_github_tools(state["github_token"])
    agent = PRAgent(state["model_name"], github_tools)

    try:
        return await agent.run(state, event_queue)
    except Exception as exc:
        logger.exception("PR agent failed for run %s", state.get("run_id"))
        await emit_event(
            state["run_id"],
            "agent_log",
            agent_name="pr",
            level="error",
            message=f"PR agent failed: {exc}",
        )
        await emit_event(
            state["run_id"],
            "run_failed",
            message=f"PR agent error: {exc}",
        )
        return {
            "final_status": "failed",
            "error_message": f"PR agent error: {exc}",
            "current_node": "pr",
            "logs": [
                AgentLog(
                    agent_name="pr",
                    level="error",
                    message=str(exc),
                    timestamp=_utc_now_iso(),
                )
            ],
        }
