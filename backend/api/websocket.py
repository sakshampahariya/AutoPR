"""WebSocket streaming for orchestration run events."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.run_manager import run_manager

logger = logging.getLogger(__name__)

ws_router = APIRouter()


@ws_router.websocket("/ws/{run_id}")
async def websocket_endpoint(websocket: WebSocket, run_id: str) -> None:
    """
    Stream agent events for a run.

    Replays any events already queued, then streams live until
    ``run_complete`` or ``run_failed``.
    """
    await websocket.accept()
    event_queue = run_manager.get_queue(run_id)

    if event_queue is None:
        await websocket.close(code=4004, reason="Run not found")
        return

    run_manager.mark_connected(run_id)

    try:
        buffered: list = []
        while not event_queue.empty():
            try:
                buffered.append(event_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        for event in buffered:
            await websocket.send_json(event)

        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping", "run_id": run_id})
                continue

            await websocket.send_json(event)

            if event.get("type") in ("run_complete", "run_failed"):
                break

    except WebSocketDisconnect:
        run_manager.mark_disconnected(run_id)
        logger.info("WebSocket disconnected for run %s", run_id)
    except Exception as exc:
        logger.exception("WebSocket error for run %s", run_id)
        try:
            await websocket.send_json(
                {"type": "error", "run_id": run_id, "message": str(exc)}
            )
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
