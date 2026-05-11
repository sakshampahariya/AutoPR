"""LangGraph StateGraph assembly and run execution."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from typing import Any, Dict, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agents.coding_agent import coding_node
from agents.pr_agent import pr_node
from agents.research_agent import research_node
from agents.testing_agent import testing_node
from core.run_manager import run_manager
from core.state import AgentState
from graph.router import route_after_coding, route_after_research, route_after_testing

logger = logging.getLogger(__name__)


def _public_state(state: AgentState | Dict[str, Any]) -> Dict[str, Any]:
    """Return a JSON-safe state snapshot without secrets for the frontend."""
    data = dict(state)
    data.pop("github_token", None)
    return data


def build_graph():
    """
    Build and compile the multi-agent orchestration graph.

    Flow:
        research → (fail|coding) → (fail|testing) → (pass→pr | retry→coding | fail→END) → END
    """
    graph = StateGraph(AgentState)

    graph.add_node("research", research_node)
    graph.add_node("coding", coding_node)
    graph.add_node("testing", testing_node)
    graph.add_node("pr", pr_node)

    graph.set_entry_point("research")

    graph.add_conditional_edges(
        "research",
        route_after_research,
        {"continue": "coding", "fail": END},
    )

    graph.add_conditional_edges(
        "coding",
        route_after_coding,
        {"continue": "testing", "fail": END},
    )

    graph.add_conditional_edges(
        "testing",
        route_after_testing,
        {
            "pass": "pr",
            "retry": "coding",
            "fail": END,
        },
    )

    graph.add_edge("pr", END)

    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


compiled_graph = build_graph()


async def run_graph(
    initial_state: AgentState,
    event_queue: asyncio.Queue,
) -> Optional[AgentState]:
    """
    Execute the compiled graph for a single run.

    Args:
        initial_state: Starting AgentState (from ``create_initial_state``).
        event_queue: Queue used for WebSocket events and state snapshots.

    Returns:
        Final merged state after the graph completes, or None if no snapshot.
    """
    run_id = initial_state["run_id"]
    config: Dict[str, Any] = {
        "configurable": {
            "thread_id": run_id,
            "event_queue": event_queue,
            "workspace_dir": tempfile.mkdtemp(prefix=f"mao_{run_id}_"),
        }
    }

    final_state: Optional[AgentState] = None

    try:
        async for state_snapshot in compiled_graph.astream(
            initial_state,
            config=config,
            stream_mode="values",
        ):
            final_state = state_snapshot  # type: ignore[assignment]
            public = _public_state(state_snapshot)
            run_manager.update_state(run_id, dict(state_snapshot))
            await event_queue.put(
                {
                    "type": "state_update",
                    "run_id": run_id,
                    "state": public,
                }
            )
    except Exception as exc:
        logger.exception("Graph execution failed for run %s", run_id)
        await event_queue.put(
            {
                "type": "run_failed",
                "run_id": run_id,
                "message": str(exc),
                "error": str(exc),
            }
        )
        raise
    finally:
        workspace = config["configurable"].get("workspace_dir")
        if workspace and os.path.exists(workspace):
            shutil.rmtree(workspace, ignore_errors=True)

    if final_state is None:
        await event_queue.put(
            {
                "type": "run_failed",
                "run_id": run_id,
                "message": "Graph finished without a final state",
                "error": "Graph finished without a final state",
            }
        )
        return None

    if final_state.get("final_status") == "success":
        await event_queue.put(
            {
                "type": "run_complete",
                "run_id": run_id,
                "pr_url": final_state.get("pr_url"),
            }
        )
    elif final_state.get("final_status") != "running":
        await event_queue.put(
            {
                "type": "run_failed",
                "run_id": run_id,
                "message": final_state.get("error_message") or "Run failed",
                "error": final_state.get("error_message") or "Run failed",
            }
        )

    return final_state
