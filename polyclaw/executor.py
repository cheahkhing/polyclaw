"""Trade Executor — places real orders on the Polymarket CLOB API."""

from __future__ import annotations

import asyncio
import os
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from polyclaw.config import PolyclawConfig
from polyclaw.models import LiveTradeResult, MarketContext, TradeSignal
from polyclaw.utils.logging import get_logger

logger = get_logger("executor")


class BaseExecutor(ABC):
    """Abstract base for executors (live and mock)."""

    @abstractmethod
    def execute(
        self, signal: TradeSignal, context: MarketContext
    ) -> Any:
        """Execute a trade signal and return a result."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""

    @abstractmethod
    def cancel_all(self) -> int:
        """Cancel all open orders. Returns number cancelled."""

    @abstractmethod
    def get_open_orders(self) -> list[dict]:
        """Return a list of open orders."""


class TradeExecutor(BaseExecutor):
    """Live trade executor that places real orders on Polymarket via py-clob-client."""

    def __init__(self, config: PolyclawConfig):
        self.config = config
        self._client: Any = None
        self._heartbeat_task: asyncio.Task | None = None
        self._init_client()

    def _init_client(self) -> None:
        """Initialise the authenticated CLOB client."""
        private_key = self.config.polymarket.private_key
        if not private_key:
            logger.warning(
                "No private key found in env var '%s' — live trading disabled",
                self.config.polymarket.private_key_env,
            )
            return

        try:
            from py_clob_client.client import ClobClient

            funder = self.config.polymarket.funder_address
            self._client = ClobClient(
                self.config.polymarket.host,
                key=private_key,
                chain_id=self.config.polymarket.chain_id,
                signature_type=self.config.polymarket.signature_type,
                funder=funder,
            )
            # Derive or create API credentials
            creds = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(creds)
            logger.info("CLOB client authenticated (L2)")
        except Exception as exc:
            logger.error("Failed to initialise CLOB client: %s", exc)
            self._client = None

    def execute(
        self, signal: TradeSignal, context: MarketContext
    ) -> LiveTradeResult:
        """Place an order on Polymarket."""
        if not self._client:
            return LiveTradeResult(
                signal=signal,
                status="failed",
                success=False,
                error="CLOB client not initialised (missing private key?)",
                timestamp=datetime.utcnow().isoformat(),
            )

        try:
            from py_clob_client.order_builder.constants import BUY, SELL
            from py_clob_client.clob_types import OrderArgs, MarketOrderArgs, OrderType

            side = BUY if signal.side == "BUY" else SELL

            if signal.order_type == "FOK":
                # Market order (Fill or Kill)
                order_args = MarketOrderArgs(
                    token_id=signal.token_id,
                    amount=signal.size,
                    side=side,
                )
                signed = self._client.create_market_order(order_args)
                resp = self._client.post_order(signed, OrderType.FOK)
            else:
                # Limit order (GTC or FAK)
                order_args = OrderArgs(
                    token_id=signal.token_id,
                    price=signal.price,
                    size=signal.size,
                    side=side,
                )
                if signal.neg_risk:
                    order_args.neg_risk = True

                signed = self._client.create_order(order_args)
                ot = OrderType.GTC if signal.order_type == "GTC" else OrderType.FOK
                resp = self._client.post_order(signed, ot)

            order_id = ""
            if isinstance(resp, dict):
                order_id = resp.get("orderID", resp.get("id", ""))

            logger.info(
                "Order placed: %s %s %s @ $%.4f (order=%s)",
                signal.side,
                signal.outcome,
                signal.market_id[:20],
                signal.price,
                order_id,
            )

            return LiveTradeResult(
                order_id=order_id,
                signal=signal,
                status="pending",
                timestamp=datetime.utcnow().isoformat(),
                success=True,
            )

        except Exception as exc:
            logger.error("Order placement failed: %s", exc)
            return LiveTradeResult(
                signal=signal,
                status="failed",
                success=False,
                error=str(exc),
                timestamp=datetime.utcnow().isoformat(),
            )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific order."""
        if not self._client:
            return False
        try:
            self._client.cancel(order_id)
            logger.info("Cancelled order %s", order_id)
            return True
        except Exception as exc:
            logger.error("Cancel failed for %s: %s", order_id, exc)
            return False

    def cancel_all(self) -> int:
        """Cancel all open orders."""
        if not self._client:
            return 0
        try:
            resp = self._client.cancel_all()
            logger.info("Cancelled all orders: %s", resp)
            return 1  # API doesn't return count
        except Exception as exc:
            logger.error("Cancel all failed: %s", exc)
            return 0

    def get_open_orders(self) -> list[dict]:
        """Return all open orders."""
        if not self._client:
            return []
        try:
            from py_clob_client.clob_types import OpenOrderParams
            orders = self._client.get_orders(OpenOrderParams())
            if isinstance(orders, list):
                return orders
            return []
        except Exception as exc:
            logger.error("Get open orders failed: %s", exc)
            return []

    async def start_heartbeat(self, interval: float = 5.0) -> None:
        """Start a heartbeat loop to keep orders alive."""
        async def _loop():
            while True:
                await asyncio.sleep(interval)
                # The CLOB client may have a keep-alive mechanism;
                # for now, just check open orders periodically
                try:
                    orders = self.get_open_orders()
                    if not orders:
                        break
                except Exception:
                    pass

        self._heartbeat_task = asyncio.create_task(_loop())

    def stop_heartbeat(self) -> None:
        """Stop the heartbeat loop."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
