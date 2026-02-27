"""WebSocket handler — manages browser connections and fans out EventBus events."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import Any

from polyclaw.models import SimEvent
from polyclaw.utils.logging import get_logger

logger = get_logger("dashboard.ws")


class WebSocketConnectionManager:
    """Manages active WebSocket connections for the dashboard.

    When the EventBus publishes a SimEvent, this manager serializes it
    and sends it to all connected browser clients.
    """

    def __init__(self) -> None:
        self.active_connections: list[Any] = []  # FastAPI WebSocket objects
        self._event_buffer: list[dict] = []  # Recent events for new connections
        self._buffer_max = 100
        self._loop: asyncio.AbstractEventLoop | None = None  # set by app startup

    async def connect(self, websocket: Any) -> None:
        """Accept a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("Dashboard client connected (%d total)", len(self.active_connections))

        # Send recent events to catch up
        for event_data in self._event_buffer[-50:]:
            try:
                await websocket.send_json(event_data)
            except Exception:
                pass

    def disconnect(self, websocket: Any) -> None:
        """Remove a disconnected WebSocket."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info("Dashboard client disconnected (%d remaining)", len(self.active_connections))

    async def broadcast(self, data: dict) -> None:
        """Send data to all connected clients."""
        # Buffer the event
        self._event_buffer.append(data)
        if len(self._event_buffer) > self._buffer_max:
            self._event_buffer = self._event_buffer[-self._buffer_max:]

        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(data)
            except Exception:
                disconnected.append(connection)

        for conn in disconnected:
            self.disconnect(conn)

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store reference to the main asyncio event loop (call from app startup)."""
        self._loop = loop

    def on_sim_event(self, event: SimEvent) -> None:
        """Sync callback for EventBus — queues broadcast for async execution.

        Called from the simulator's background thread, so we use
        call_soon_threadsafe to schedule the async broadcast on the
        main event loop.
        """
        data = {
            "type": event.type,
            "timestamp": event.timestamp,
            "data": event.data,
        }
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(
                asyncio.ensure_future, self.broadcast(data)
            )
        else:
            # No running loop — buffer for later
            self._event_buffer.append(data)
            if len(self._event_buffer) > self._buffer_max:
                self._event_buffer = self._event_buffer[-self._buffer_max:]
