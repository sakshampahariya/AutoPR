"""Conditional routing for the LangGraph orchestration pipeline."""

from __future__ import annotations

import os

from core.state import AgentState

MAX_RETRIES = int(os.getenv("AGENT_MAX_RETRIES", "3"))


def route_after_testing(state: AgentState) -> str:
    """
    Conditional edge router after the Testing node.

    Returns:
        ``pass`` — tests succeeded, proceed to PR.
        ``retry`` — tests failed and retries remain, loop to Coding.
        ``fail`` — no result or max retries exceeded, end the graph.
    """
    test_result = state.get("test_result")
    if test_result is None:
        return "fail"
    if test_result["status"] == "pass":
        return "pass"
    if state.get("retry_count", 0) < MAX_RETRIES:
        return "retry"
    return "fail"


def route_after_research(state: AgentState) -> str:
    """
    Route after Research — skip to END if research already marked the run failed.

    Returns:
        ``continue`` — proceed to Coding.
        ``fail`` — terminate the graph.
    """
    if state.get("final_status") == "failed":
        return "fail"
    return "continue"


def route_after_coding(state: AgentState) -> str:
    """
    Route after Coding — skip testing if coding failed or produced no changes.

    Returns:
        ``continue`` — proceed to Testing.
        ``fail`` — terminate the graph.
    """
    if state.get("final_status") == "failed":
        return "fail"
    if not state.get("file_changes"):
        return "fail"
    return "continue"
