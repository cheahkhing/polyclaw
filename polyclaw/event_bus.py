"""EventBus — in-process pub/sub for simulation events."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable

from polyclaw.models import SimEvent
from polyclaw.utils.logging import get_logger

logger = get_logger("event_bus")


class EventBus:
    """Simple in-process pub/sub connecting the simulation engine to the dashboard.

    Supports both sync and async subscribers. Wildcard '*' subscribers
    receive all events.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._async_subscribers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: str, callback: Callable) -> None:
        """Register a synchronous callback for an event type.

        Use '*' to receive all events.
        """
        self._subscribers[event_type].append(callback)

    def subscribe_async(self, event_type: str, callback: Callable) -> None:
        """Register an async callback for an event type."""
        self._async_subscribers[event_type].append(callback)

    def publish(self, event: SimEvent) -> None:
        """Notify all sync subscribers for this event type + wildcard."""
        for cb in self._subscribers.get(event.type, []):
            try:
                cb(event)
            except Exception as exc:
                logger.error("EventBus sync callback error for '%s': %s", event.type, exc)

        for cb in self._subscribers.get("*", []):
            try:
                cb(event)
            except Exception as exc:
                logger.error("EventBus wildcard callback error: %s", exc)

    async def publish_async(self, event: SimEvent) -> None:
        """Notify all async subscribers for this event type + wildcard."""
        # First run sync subscribers
        self.publish(event)

        # Then run async subscribers
        for cb in self._async_subscribers.get(event.type, []):
            try:
                await cb(event)
            except Exception as exc:
                logger.error("EventBus async callback error for '%s': %s", event.type, exc)

        for cb in self._async_subscribers.get("*", []):
            try:
                await cb(event)
            except Exception as exc:
                logger.error("EventBus async wildcard callback error: %s", exc)

    def clear(self) -> None:
        """Remove all subscribers."""
        self._subscribers.clear()
        self._async_subscribers.clear()
