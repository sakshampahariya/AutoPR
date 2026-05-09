"""Coding Agent — generates patches from the research context brief."""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from core.config import get_settings
from core.run_manager import emit_event
from core.state import AgentLog, AgentState, FileChange
from tools.github_tools import GitHubTools, get_github_tools

logger = logging.getLogger(__name__)

CODING_SYSTEM_PROMPT_BASE = """You are an expert Python software engineer. Your task is to write the
minimal, correct code fix for a GitHub issue based on a provided
Context Brief.

You will be given:
  - A structured Context Brief from the Research Agent
  - The full content of relevant files (in the user message)

Rules:
  1. Make the MINIMAL change necessary to fix the issue. Do NOT refactor
     unrelated code.
  2. Follow the existing code style (PEP 8, naming conventions,
     docstring format).
  3. If the issue requests new functionality, write tests for it.
  4. ALWAYS return your output in this EXACT JSON format:
     {
       "patch_summary": "One-sentence description of the fix",
       "file_changes": [
         {
           "file_path": "relative/path/to/file.py",
           "patched_content": "...FULL file content after fix...",
           "change_description": "What was changed and why"
         }
       ],
       "new_test_file": {
         "file_path": "tests/test_new_feature.py",
         "content": "...test file content..."
       }
     }

Use null for "new_test_file" when no new test file is needed.
Return ONLY valid JSON (no markdown fences)."""

CODING_RETRY_APPENDIX = """
IMPORTANT: Your previous fix failed the test suite. Here is the
failure output:
--- TEST FAILURE LOG ---
{stdout}
{stderr}
--- END LOG ---
Analyze the failure, identify what was wrong in your previous patch,
and produce a corrected fix."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_log(message: str, level: str = "info") -> AgentLog:
    return AgentLog(
        agent_name="coding",
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


class CodingAgent:
    """Generates file patches and writes them to a persistent workspace directory."""

    def __init__(
        self,
        model_name: str,
        github_tools: GitHubTools,
        workspace_dir: str,
    ) -> None:
        self._model_name = model_name
        self._github = github_tools
        self._workspace_dir = Path(workspace_dir)
        self._workspace_dir.mkdir(parents=True, exist_ok=True)

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

    def _build_system_prompt(self, state: AgentState) -> str:
        """Build the Coding Agent system prompt, including retry context if needed."""
        prompt = CODING_SYSTEM_PROMPT_BASE
        retry_count = state.get("retry_count", 0)
        test_result = state.get("test_result")

        if retry_count > 0 and test_result is not None:
            prompt += CODING_RETRY_APPENDIX.format(
                stdout=test_result.get("stdout", ""),
                stderr=test_result.get("stderr", ""),
            )
        return prompt

    def _parse_llm_response(self, response_text: str) -> dict:
        """
        Parse the LLM JSON response into patch_summary and file change entries.

        Raises:
            ValueError: If JSON is missing or malformed.
        """
        text = response_text.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence:
            text = fence.group(1)

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    raise ValueError(
                        f"Coding agent returned malformed JSON: {exc}"
                    ) from exc
            else:
                raise ValueError(
                    f"Coding agent returned malformed JSON: {exc}"
                ) from exc

        if not isinstance(data, dict):
            raise ValueError("Coding agent response must be a JSON object")

        file_changes = data.get("file_changes")
        if not isinstance(file_changes, list):
            raise ValueError("Coding agent JSON must include a 'file_changes' array")

        entries: List[dict] = []
        for item in file_changes:
            if not isinstance(item, dict):
                continue
            path = item.get("file_path")
            content = item.get("patched_content")
            if not path or content is None:
                raise ValueError(
                    "Each file_changes entry requires 'file_path' and 'patched_content'"
                )
            entries.append(
                {
                    "file_path": str(path).lstrip("/"),
                    "patched_content": content,
                    "change_description": item.get("change_description", ""),
                }
            )

        new_test = data.get("new_test_file")
        if isinstance(new_test, dict) and new_test.get("file_path") and new_test.get("content") is not None:
            entries.append(
                {
                    "file_path": str(new_test["file_path"]).lstrip("/"),
                    "patched_content": new_test["content"],
                    "change_description": "New test file generated by Coding Agent",
                }
            )

        if not entries:
            raise ValueError("Coding agent returned no file changes")

        return {
            "patch_summary": data.get("patch_summary", "Code fix generated"),
            "file_entries": entries,
        }

    @staticmethod
    def _unified_diff(
        original: str,
        patched: str,
        file_path: str,
    ) -> str:
        return "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                patched.splitlines(keepends=True),
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
            )
        )

    def _write_workspace_file(self, file_path: str, content: str) -> None:
        dest = self._workspace_dir / file_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    async def run(
        self,
        state: AgentState,
        event_queue: Optional[asyncio.Queue] = None,
    ) -> dict:
        """
        Generate patches via LLM and materialize them in the workspace.

        Returns:
            Partial AgentState update with file_changes and patch_summary.
        """
        run_id = state["run_id"]
        owner = state["repo_owner"]
        repo = state["repo_name"]
        logs: List[AgentLog] = []

        await self._emit(run_id, event_queue, "node_start", node="coding")

        system_prompt = self._build_system_prompt(state)
        codebase_context = state.get("codebase_context") or ""

        await self._emit(
            run_id,
            event_queue,
            "agent_log",
            agent_name="coding",
            level="info",
            message="Generating code fix...",
        )
        logs.append(_make_log("Generating code fix..."))

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(
                content=(
                    f"Issue: {state.get('issue_title') or ''}\n\n"
                    f"Context Brief and file contents:\n{codebase_context}"
                )
            ),
        ]
        response = await self._llm.ainvoke(messages)
        raw_text = _message_content_to_str(response.content)
        parsed = self._parse_llm_response(raw_text)

        file_changes: List[FileChange] = []
        combined_diff_parts: List[str] = []

        for entry in parsed["file_entries"]:
            file_path = entry["file_path"]
            patched_content = entry["patched_content"]
            change_description = entry.get("change_description", "")

            try:
                original_content = await asyncio.to_thread(
                    self._github.read_file, owner, repo, file_path
                )
            except FileNotFoundError:
                original_content = ""

            diff_text = self._unified_diff(original_content, patched_content, file_path)
            self._write_workspace_file(file_path, patched_content)

            await self._emit(
                run_id,
                event_queue,
                "agent_log",
                agent_name="coding",
                level="info",
                message=f"Patch applied: {file_path}",
            )
            logs.append(_make_log(f"Patch applied: {file_path}"))

            file_changes.append(
                FileChange(
                    file_path=file_path,
                    original_content=original_content,
                    patched_content=patched_content,
                    diff=diff_text,
                    change_description=change_description,
                )
            )
            combined_diff_parts.append(diff_text)

        full_diff = "\n".join(combined_diff_parts)
        await self._emit(
            run_id,
            event_queue,
            "diff_ready",
            diff=full_diff,
        )

        await self._emit(run_id, event_queue, "node_complete", node="coding")
        logs.append(_make_log("Coding phase complete."))

        return {
            "file_changes": file_changes,
            "patch_summary": parsed["patch_summary"],
            "retry_count": state.get("retry_count", 0),
            "current_node": "coding",
            "logs": logs,
        }


def _resolve_workspace_dir(config: RunnableConfig) -> str:
    """
    Return a workspace directory that persists across coding retries.

    When ``config["configurable"]`` is a mutable dict (set by the orchestrator),
    stores ``workspace_dir`` on it so retries reuse the same path.
    """
    configurable = config.get("configurable")
    if isinstance(configurable, dict):
        existing = configurable.get("workspace_dir")
        if existing and os.path.isdir(str(existing)):
            return str(existing)
        workspace_dir = tempfile.mkdtemp(prefix="orchestrator_ws_")
        configurable["workspace_dir"] = workspace_dir
        return workspace_dir

    return tempfile.mkdtemp(prefix="orchestrator_ws_")


async def coding_node(state: AgentState, config: RunnableConfig) -> dict:
    """LangGraph node function for the Coding Agent."""
    configurable = config.get("configurable") or {}
    event_queue: Optional[asyncio.Queue] = configurable.get("event_queue")
    workspace_dir = _resolve_workspace_dir(config)
    github_tools = get_github_tools(state["github_token"])
    agent = CodingAgent(state["model_name"], github_tools, workspace_dir)

    try:
        return await agent.run(state, event_queue)
    except Exception as exc:
        logger.exception("Coding agent failed for run %s", state.get("run_id"))
        await emit_event(
            state["run_id"],
            "agent_log",
            agent_name="coding",
            level="error",
            message=f"Coding agent failed: {exc}",
        )
        return {
            "final_status": "failed",
            "error_message": f"Coding agent error: {exc}",
            "current_node": "coding",
            "logs": [
                AgentLog(
                    agent_name="coding",
                    level="error",
                    message=str(exc),
                    timestamp=_utc_now_iso(),
                )
            ],
        }
