"""RiskGate — pre-trade validation against risk limits."""

from __future__ import annotations

from polyclaw.config import RiskConfig
from polyclaw.models import RiskVerdict, TradeSignal
from polyclaw.utils.logging import get_logger

logger = get_logger("risk")


class RiskGate:
    """Validates trade signals against risk configuration before execution."""

    def __init__(
        self,
        config: RiskConfig,
        get_open_position_count: callable = None,
        get_today_trade_count: callable = None,
        get_balance: callable = None,
    ):
        self.config = config
        self._get_open_position_count = get_open_position_count or (lambda: 0)
        self._get_today_trade_count = get_today_trade_count or (lambda: 0)
        self._get_balance = get_balance or (lambda: float("inf"))

    def check(self, signal: TradeSignal) -> RiskVerdict:
        """Validate a signal against all risk rules. Returns verdict."""
        # 1. Confidence check
        if signal.confidence < self.config.min_confidence:
            reason = (
                f"Confidence {signal.confidence:.2f} < min {self.config.min_confidence:.2f}"
            )
            logger.debug("Risk rejected: %s", reason)
            return RiskVerdict(approved=False, reason=reason, signal=signal)

        # 2. Position size check
        trade_cost = signal.price * signal.size
        if trade_cost > self.config.max_position_size:
            reason = (
                f"Trade cost ${trade_cost:.2f} > max position ${self.config.max_position_size:.2f}"
            )
            logger.debug("Risk rejected: %s", reason)
            return RiskVerdict(approved=False, reason=reason, signal=signal)

        # 3. Max open positions
        if signal.side == "BUY":
            open_count = self._get_open_position_count()
            if open_count >= self.config.max_open_positions:
                reason = (
                    f"Open positions {open_count} >= max {self.config.max_open_positions}"
                )
                logger.debug("Risk rejected: %s", reason)
                return RiskVerdict(approved=False, reason=reason, signal=signal)

        # 4. Daily trade limit
        today_count = self._get_today_trade_count()
        if today_count >= self.config.max_daily_trades:
            reason = (
                f"Daily trades {today_count} >= max {self.config.max_daily_trades}"
            )
            logger.debug("Risk rejected: %s", reason)
            return RiskVerdict(approved=False, reason=reason, signal=signal)

        # 5. Balance check for buys
        if signal.side == "BUY":
            balance = self._get_balance()
            if trade_cost > balance:
                reason = (
                    f"Insufficient balance: need ${trade_cost:.2f}, have ${balance:.2f}"
                )
                logger.debug("Risk rejected: %s", reason)
                return RiskVerdict(approved=False, reason=reason, signal=signal)

        logger.debug("Risk approved: %s %s @ $%.4f", signal.side, signal.outcome, signal.price)
        return RiskVerdict(approved=True, signal=signal)
