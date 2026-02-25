"""Mock Executor — paper-trade recorder with no real order submission."""

from __future__ import annotations

from datetime import datetime

from polyclaw.config import PolyclawConfig
from polyclaw.executor import BaseExecutor
from polyclaw.models import (
    MarketContext,
    MockTradeResult,
    Position,
    TradeSignal,
)
from polyclaw.utils.logging import get_logger

logger = get_logger("mock_executor")


class MockExecutor(BaseExecutor):
    """Drop-in replacement for TradeExecutor.

    Records trades without submitting them. Simulates fills at the current
    midpoint (or specified price) with configurable slippage. Tracks a virtual
    portfolio balance.
    """

    def __init__(
        self,
        config: PolyclawConfig | None = None,
        starting_balance: float | None = None,
        slippage_bps: int | None = None,
    ):
        sb = starting_balance
        sl = slippage_bps
        if config:
            sb = sb or config.mock.starting_balance
            sl = sl if sl is not None else config.mock.slippage_bps
        self.balance: float = sb or 1000.0
        self.slippage_bps: int = sl if sl is not None else 10
        self.positions: dict[str, Position] = {}
        self.trade_log: list[MockTradeResult] = []
        self._next_trade_id: int = 1
        self._open_orders: list[dict] = []

    def execute(
        self, signal: TradeSignal, context: MarketContext
    ) -> MockTradeResult:
        """Simulate a trade execution."""
        now = datetime.utcnow().isoformat()

        # Simulate fill price with slippage
        slippage = signal.price * (self.slippage_bps / 10000)
        if signal.side == "BUY":
            fill_price = signal.price + slippage
        else:
            fill_price = signal.price - slippage
        fill_price = max(0.001, min(0.999, fill_price))

        # Calculate cost
        cost = fill_price * signal.size

        # Balance check for buys
        if signal.side == "BUY" and cost > self.balance:
            return MockTradeResult(
                signal=signal,
                fill_price=fill_price,
                slippage=slippage,
                balance_after=self.balance,
                timestamp=now,
                success=False,
                error=f"Insufficient balance: need ${cost:.2f}, have ${self.balance:.2f}",
            )

        # Execute
        if signal.side == "BUY":
            self.balance -= cost
        else:
            self.balance += cost

        trade_id = self._next_trade_id
        self._next_trade_id += 1

        # Update positions
        pos_key = f"{signal.market_id}:{signal.outcome}"
        if signal.side == "BUY":
            if pos_key in self.positions:
                pos = self.positions[pos_key]
                # Average in
                total_size = pos.size + signal.size
                pos.entry_price = (
                    (pos.entry_price * pos.size + fill_price * signal.size)
                    / total_size
                )
                pos.size = total_size
            else:
                self.positions[pos_key] = Position(
                    market_id=signal.market_id,
                    token_id=signal.token_id,
                    outcome=signal.outcome,
                    entry_price=fill_price,
                    size=signal.size,
                    current_price=fill_price,
                    unrealized_pnl=0.0,
                    strategy=signal.strategy,
                    opened_at=now,
                )
        elif signal.side == "SELL":
            if pos_key in self.positions:
                pos = self.positions[pos_key]
                pos.size -= signal.size
                if pos.size <= 0:
                    # Close position
                    pos.closed_at = now
                    pos.exit_price = fill_price
                    pos.realized_pnl = (fill_price - pos.entry_price) * signal.size
                    del self.positions[pos_key]

        result = MockTradeResult(
            trade_id=trade_id,
            signal=signal,
            fill_price=fill_price,
            slippage=slippage,
            balance_after=self.balance,
            timestamp=now,
            success=True,
        )
        self.trade_log.append(result)

        logger.info(
            "Mock trade #%d: %s %s %s @ $%.4f (fill=$%.4f, bal=$%.2f)",
            trade_id,
            signal.side,
            signal.outcome,
            signal.market_title[:30] if signal.market_title else signal.market_id[:20],
            signal.price,
            fill_price,
            self.balance,
        )
        return result

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a mock order."""
        self._open_orders = [
            o for o in self._open_orders if o.get("id") != order_id
        ]
        return True

    def cancel_all(self) -> int:
        """Cancel all mock orders."""
        count = len(self._open_orders)
        self._open_orders.clear()
        return count

    def get_open_orders(self) -> list[dict]:
        """Return mock open orders."""
        return list(self._open_orders)

    def get_open_positions(self) -> list[Position]:
        """Return list of currently open positions."""
        return list(self.positions.values())

    def update_position_prices(self, prices: dict[str, float]) -> None:
        """Update current prices and unrealized P&L for open positions.

        Args:
            prices: mapping of token_id → current midpoint price.
        """
        for pos in self.positions.values():
            if pos.token_id in prices:
                pos.current_price = prices[pos.token_id]
                pos.unrealized_pnl = (pos.current_price - pos.entry_price) * pos.size

    def resolve_market(
        self, market_id: str, winning_outcome: str
    ) -> float:
        """Resolve a market and realise P&L for any matching positions.

        Returns total realized P&L from this resolution.
        """
        total_pnl = 0.0
        to_close = [
            key
            for key in list(self.positions.keys())
            if key.startswith(f"{market_id}:")
        ]

        for key in to_close:
            pos = self.positions[key]
            if pos.outcome == winning_outcome:
                # Winner: payout is $1.00 per share
                pnl = (1.0 - pos.entry_price) * pos.size
            else:
                # Loser: payout is $0.00
                pnl = -pos.entry_price * pos.size

            pos.realized_pnl = pnl
            pos.exit_price = 1.0 if pos.outcome == winning_outcome else 0.0
            pos.closed_at = datetime.utcnow().isoformat()

            self.balance += pos.exit_price * pos.size
            total_pnl += pnl

            del self.positions[key]
            logger.info(
                "Market resolved: %s outcome=%s, pos=%s, pnl=$%.2f",
                market_id[:20],
                winning_outcome,
                pos.outcome,
                pnl,
            )

        return total_pnl
