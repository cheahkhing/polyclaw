"""WebSocket Streaming Manager — persistent connections to all Polymarket WS channels."""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from typing import Any, Callable, Coroutine

import websockets
import websockets.client

from polyclaw.config import PolyclawConfig
from polyclaw.utils.logging import get_logger

logger = get_logger("streaming")

# Channel endpoints
WS_MARKET = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
WS_USER = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
WS_SPORTS = "wss://sports-api.polymarket.com/ws"
WS_RTDS = "wss://ws-live-data.polymarket.com"

Callback = Callable[[dict[str, Any]], Coroutine[Any, Any, None] | None]


class WebSocketManager:
    """Manages persistent WebSocket connections with auto-reconnect and heartbeat."""

    def __init__(self, config: PolyclawConfig):
        self.config = config
        self.subscribers: dict[str, list[Callback]] = defaultdict(list)
        self._connections: dict[str, Any] = {}
        self._running = False
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Public: connect to channels
    # ------------------------------------------------------------------

    async def connect_market(self, token_ids: list[str]) -> None:
        """Connect to the Market channel and subscribe to given tokens."""
        ws = await self._connect_with_retry(WS_MARKET, "market")
        sub_msg = {
            "assets_ids": token_ids,
            "type": "market",
            "custom_feature_enabled": True,
        }
        await ws.send(json.dumps(sub_msg))
        logger.info("Market WS: subscribed to %d tokens", len(token_ids))
        self._spawn(self._heartbeat_loop(ws, interval=10, channel="market"))
        self._spawn(self._listen(ws, "market"))

    async def connect_user(
        self, condition_ids: list[str], api_creds: dict
    ) -> None:
        """Connect to the User channel (requires L2 auth)."""
        ws = await self._connect_with_retry(WS_USER, "user")
        sub_msg = {
            "auth": api_creds,
            "markets": condition_ids,
            "type": "user",
        }
        await ws.send(json.dumps(sub_msg))
        logger.info("User WS: subscribed to %d markets", len(condition_ids))
        self._spawn(self._heartbeat_loop(ws, interval=10, channel="user"))
        self._spawn(self._listen(ws, "user"))

    async def connect_sports(self) -> None:
        """Connect to the Sports channel (auto-subscribes, no message needed)."""
        ws = await self._connect_with_retry(WS_SPORTS, "sports")
        logger.info("Sports WS: connected (auto-subscribed)")
        self._spawn(self._listen(ws, "sports"))

    async def connect_rtds(
        self, crypto_symbols: list[str] | None = None
    ) -> None:
        """Connect to RTDS for crypto prices and comments."""
        ws = await self._connect_with_retry(WS_RTDS, "rtds")
        subs: list[dict[str, Any]] = [
            {"topic": "crypto_prices", "type": "update"}
        ]
        if crypto_symbols:
            subs[0]["filters"] = ",".join(crypto_symbols)
        subs.append({"topic": "comments", "type": "comment_created"})
        await ws.send(
            json.dumps({"action": "subscribe", "subscriptions": subs})
        )
        logger.info("RTDS WS: subscribed (crypto=%s)", crypto_symbols)
        self._spawn(self._heartbeat_loop(ws, interval=5, channel="rtds"))
        self._spawn(self._listen(ws, "rtds"))

    async def subscribe_dynamic(
        self, channel: str, token_ids: list[str]
    ) -> None:
        """Add tokens to an existing Market channel connection."""
        ws = self._connections.get("market")
        if ws is None:
            logger.warning("No active Market WS; call connect_market first")
            return
        await ws.send(
            json.dumps(
                {
                    "assets_ids": token_ids,
                    "operation": "subscribe",
                    "custom_feature_enabled": True,
                }
            )
        )
        logger.info("Dynamic subscribe: %d tokens", len(token_ids))

    # ------------------------------------------------------------------
    # Public: event subscription
    # ------------------------------------------------------------------

    def on(self, event_type: str, callback: Callback) -> None:
        """Register a callback for a specific event type."""
        self.subscribers[event_type].append(callback)

    # ------------------------------------------------------------------
    # Public: lifecycle
    # ------------------------------------------------------------------

    async def start(self, token_ids: list[str] | None = None) -> None:
        """Start configured channels."""
        self._running = True
        cfg = self.config.streaming

        if "market" in cfg.channels and token_ids:
            await self.connect_market(token_ids)
        if "sports" in cfg.channels:
            await self.connect_sports()
        if "rtds" in cfg.channels:
            await self.connect_rtds(cfg.rtds_crypto_symbols or None)

    async def stop(self) -> None:
        """Gracefully close all connections."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        for name, ws in list(self._connections.items()):
            try:
                await ws.close()
            except Exception:
                pass
            logger.info("Closed %s WS", name)
        self._connections.clear()
        self._tasks.clear()

    # ------------------------------------------------------------------
    # Internal: connection, listening, heartbeat
    # ------------------------------------------------------------------

    async def _connect_with_retry(self, url: str, channel: str) -> Any:
        """Connect with exponential backoff."""
        delay = self.config.streaming.reconnect_delay_ms / 1000
        max_delay = self.config.streaming.reconnect_max_delay_ms / 1000

        while True:
            try:
                ws = await websockets.connect(url)
                self._connections[channel] = ws
                return ws
            except Exception as exc:
                logger.warning(
                    "%s WS connect failed: %s — retrying in %.1fs",
                    channel,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)

    async def _listen(self, ws: Any, channel: str) -> None:
        """Listen for messages and dispatch to subscribers."""
        try:
            async for raw_msg in ws:
                if not self._running:
                    break
                try:
                    data = json.loads(raw_msg) if isinstance(raw_msg, str) else {}
                except json.JSONDecodeError:
                    # Handle ping/pong text frames from Sports channel
                    if raw_msg == "ping":
                        await ws.send("pong")
                    continue

                # Determine event type from the message
                event_type = self._extract_event_type(data, channel)
                await self._dispatch(event_type, data)

        except websockets.exceptions.ConnectionClosed:
            logger.warning("%s WS disconnected", channel)
            if self._running:
                await self._reconnect(channel)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("%s WS error: %s", channel, exc)
            if self._running:
                await self._reconnect(channel)

    async def _heartbeat_loop(
        self, ws: Any, interval: int, channel: str
    ) -> None:
        """Send periodic PING to keep the connection alive."""
        try:
            while self._running:
                await asyncio.sleep(interval)
                try:
                    await ws.send("PING")
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    async def _reconnect(self, channel: str) -> None:
        """Reconnect a dropped channel."""
        logger.info("Attempting reconnect for %s", channel)
        # This is a simplified reconnect; a production system would
        # re-subscribe with the same parameters.
        # For the POC, just log and stop.
        logger.warning("Reconnect for %s not fully implemented in POC", channel)

    def _extract_event_type(self, data: dict, channel: str) -> str:
        """Determine the event type from a WS message."""
        if channel == "market":
            # Market channel messages have an "event_type" or list structure
            return data.get("event_type", data.get("type", "market_update"))
        if channel == "user":
            return data.get("type", "user_update")
        if channel == "sports":
            return "sport_result"
        if channel == "rtds":
            topic = data.get("topic", "")
            if topic == "crypto_prices":
                return "crypto_prices"
            if topic == "comments":
                return "comment_created"
            return f"rtds_{topic}" if topic else "rtds_update"
        return "unknown"

    async def _dispatch(self, event_type: str, data: dict) -> None:
        """Call all registered callbacks for *event_type*."""
        for cb in self.subscribers.get(event_type, []):
            try:
                result = cb(data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.error("Callback error for %s: %s", event_type, exc)

        # Also dispatch to wildcard subscribers
        for cb in self.subscribers.get("*", []):
            try:
                result = cb(data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.error("Wildcard callback error: %s", exc)

    def _spawn(self, coro: Any) -> None:
        """Create a background task and track it."""
        task = asyncio.ensure_future(coro)
        self._tasks.append(task)
