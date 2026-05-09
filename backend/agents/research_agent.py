"""Research Agent — analyzes issues and builds a codebase context brief."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool

from core.config import get_settings
from core.run_manager import emit_event
from core.state import AgentLog, AgentState
from tools.github_tools import GitHubTools, get_github_tools

logger = logging.getLogger(__name__)

RESEARCH_SYSTEM_PROMPT = """You are an expert software engineering analyst. Your task is to fully
understand a GitHub issue and the relevant portion of the codebase so
that a Coding Agent can fix the issue without additional context.

You will be given:
  - The full text of a GitHub issue (title + body)
  - The top-level repository structure

Your job:
  1. Identify the ROOT CAUSE of the issue based on the description.
  2. Identify WHICH FILES are most likely responsible for the bug.
  3. Use the search_codebase tool when you need to locate symbols or patterns.
  4. Identify the EXACT functions, classes, or lines that need to change.
  5. Check for RELATED TESTS that already exist for this code path.
  6. Produce a structured "Context Brief" in this exact JSON format:
     {
       "root_cause_analysis": "...",
       "files_to_modify": ["path/to/file.py", ...],
       "relevant_code_snippets": {"path/to/file.py": "...snippet..."},
       "existing_tests": ["tests/test_file.py"],
       "suggested_approach": "...",
       "dependencies_to_check": ["module_name", ...]
     }

Return ONLY valid JSON for the Context Brief as your final message (no markdown fences)."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_log(message: str, level: str = "info") -> AgentLog:
    return AgentLog(
        agent_name="research",
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


def _parse_context_brief(text: str) -> dict:
    """Extract Context Brief JSON from LLM output."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise ValueError("LLM response did not contain valid Context Brief JSON")


class ResearchAgent:
    """Fetches issue/repo data and produces a structured context brief for coding."""

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

    async def run(
        self,
        state: AgentState,
        event_queue: Optional[asyncio.Queue] = None,
    ) -> dict:
        """
        Execute the research workflow and return a partial AgentState update.

        Args:
            state: Current LangGraph state.
            event_queue: Optional event queue from LangGraph config.

        Returns:
            Partial state dict for LangGraph merging.
        """
        run_id = state["run_id"]
        owner = state["repo_owner"]
        repo = state["repo_name"]
        issue_number = state["issue_number"]
        logs: List[AgentLog] = []

        await self._emit(run_id, event_queue, "node_start", node="research")

        issue_data = await asyncio.to_thread(
            self._github.get_issue_details, owner, repo, issue_number
        )
        repo_structure = await asyncio.to_thread(
            self._github.get_repo_structure, owner, repo
        )

        await self._emit(
            run_id,
            event_queue,
            "agent_log",
            agent_name="research",
            level="info",
            message="Analyzing issue and repository structure...",
        )
        logs.append(_make_log("Analyzing issue and repository structure..."))

        analysis = await self._analyze_with_llm(issue_data, repo_structure, state)

        relevant_files: List[str] = list(analysis.get("relevant_files") or [])
        context_sections: List[str] = [
            "# Context Brief",
            f"## Root Cause\n{analysis.get('root_cause_analysis', '')}",
            f"## Suggested Approach\n{analysis.get('suggested_approach', '')}",
        ]

        brief_json = analysis.get("context_brief")
        if brief_json:
            context_sections.append(f"## Full Brief (JSON)\n{brief_json}")

        for file_path in relevant_files:
            await self._emit(
                run_id,
                event_queue,
                "agent_log",
                agent_name="research",
                level="info",
                message=f"Reading file: {file_path}",
            )
            logs.append(_make_log(f"Reading file: {file_path}"))
            try:
                content = await asyncio.to_thread(
                    self._github.read_file, owner, repo, file_path
                )
                context_sections.append(
                    f"## File: {file_path}\n```\n{content}\n```"
                )
            except (FileNotFoundError, ValueError) as exc:
                warn = f"Could not read {file_path}: {exc}"
                logger.warning(warn)
                logs.append(_make_log(warn, level="warning"))
                context_sections.append(f"## File: {file_path}\n(unavailable: {exc})")

        codebase_context = "\n\n".join(context_sections)
        await self._emit(run_id, event_queue, "node_complete", node="research")
        logs.append(_make_log("Research phase complete."))

        return {
            "issue_title": issue_data.get("title"),
            "issue_body": issue_data.get("body"),
            "relevant_files": relevant_files,
            "codebase_context": codebase_context,
            "repo_structure": repo_structure,
            "current_node": "research",
            "logs": logs,
        }

    async def _analyze_with_llm(
        self,
        issue_data: dict,
        repo_structure: str,
        state: AgentState,
    ) -> dict:
        """
        Run the LLM with ``search_codebase`` tool binding and parse the Context Brief.

        Returns:
            Dict with relevant_files, root_cause_analysis, suggested_approach,
            and context_brief (JSON string).
        """
        owner = state["repo_owner"]
        repo = state["repo_name"]

        def _search_codebase(query: str) -> str:
            cleaned = (query or "").strip()
            if not cleaned:
                return json.dumps({"error": "empty query"}, indent=2)
            try:
                results = self._github.search_codebase(owner, repo, cleaned)
                return json.dumps(results, indent=2)
            except Exception as exc:
                return json.dumps(
                    {"error": f"search_codebase failed: {exc}"}, indent=2
                )

        search_tool = StructuredTool.from_function(
            func=_search_codebase,
            name="search_codebase",
            description=(
                "Search the repository for code matching a query. "
                "Returns paths, URLs, and snippets."
            ),
        )

        llm_with_tools = self._llm.bind_tools([search_tool])
        human_content = (
            f"Issue title: {issue_data.get('title', '')}\n\n"
            f"Issue body:\n{issue_data.get('body', '')}\n\n"
            f"Repository structure:\n{repo_structure}"
        )
        messages: list = [
            SystemMessage(content=RESEARCH_SYSTEM_PROMPT),
            HumanMessage(content=human_content),
        ]

        async def _invoke_with_tools(active_messages: list) -> AIMessage | None:
            response: AIMessage | None = None
            for _ in range(6):
                response = await llm_with_tools.ainvoke(active_messages)
                if not getattr(response, "tool_calls", None):
                    return response

                active_messages.append(response)
                for tool_call in response.tool_calls:
                    tool_name = tool_call.get("name")
                    tool_id = tool_call.get("id", "")
                    args = tool_call.get("args") or {}
                    if tool_name != "search_codebase":
                        tool_result = json.dumps(
                            {"error": f"Unknown tool: {tool_name}"}
                        )
                    else:
                        query = args.get("query", "")
                        tool_result = await asyncio.to_thread(_search_codebase, query)
                    active_messages.append(
                        ToolMessage(content=tool_result, tool_call_id=tool_id)
                    )
            return response

        response = await _invoke_with_tools(messages)
        if response is None:
            raise RuntimeError("LLM returned no response during research analysis")

        raw_text = _message_content_to_str(response.content)
        try:
            brief = _parse_context_brief(raw_text)
        except ValueError:
            messages.append(
                HumanMessage(
                    content=(
                        "Your previous response was not valid JSON. "
                        "Return ONLY the Context Brief JSON object, no markdown or extra text."
                    )
                )
            )
            response = await _invoke_with_tools(messages)
            if response is None:
                raise RuntimeError("LLM returned no response during research analysis")
            raw_text = _message_content_to_str(response.content)
            brief = _parse_context_brief(raw_text)

        relevant_files = list(brief.get("files_to_modify") or [])
        if not relevant_files and brief.get("relevant_code_snippets"):
            relevant_files = list(brief["relevant_code_snippets"].keys())

        return {
            "relevant_files": relevant_files,
            "root_cause_analysis": brief.get("root_cause_analysis", ""),
            "suggested_approach": brief.get("suggested_approach", ""),
            "context_brief": json.dumps(brief, indent=2),
        }


async def research_node(state: AgentState, config: RunnableConfig) -> dict:
    """LangGraph node function for the Research Agent."""
    configurable = config.get("configurable") or {}
    event_queue: Optional[asyncio.Queue] = configurable.get("event_queue")
    github_tools = get_github_tools(state["github_token"])
    agent = ResearchAgent(state["model_name"], github_tools)

    try:
        return await agent.run(state, event_queue)
    except Exception as exc:
        logger.exception("Research agent failed for run %s", state.get("run_id"))
        await emit_event(
            state["run_id"],
            "agent_log",
            agent_name="research",
            level="error",
            message=f"Research agent failed: {exc}",
        )
        return {
            "final_status": "failed",
            "error_message": f"Research agent error: {exc}",
            "current_node": "research",
            "logs": [
                AgentLog(
                    agent_name="research",
                    level="error",
                    message=str(exc),
                    timestamp=_utc_now_iso(),
                )
            ],
        }
