"""Sports Volatility Strategy — trades sports events with high vol resolving soon."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from polyclaw.config import PolyclawConfig
from polyclaw.models import MarketContext, Position, TradeSignal
from polyclaw.strategy import BaseStrategy
from polyclaw.utils.logging import get_logger

logger = get_logger("strategy.sports_volatility")

DEFAULT_CONFIG = {
    "max_days_to_resolution": 7,
    "min_volatility": 0.03,
    "max_spread": 0.06,
    "price_window_size": 20,
    "min_volume_24hr": 5000,
    "tags": ["sports", "nba", "nfl", "mlb", "nhl", "soccer", "mma", "tennis",
             "boxing", "cricket", "f1", "rugby"],
    "take_profit_pct": 0.10,
    "stop_loss_pct": 0.15,
    "mean_reversion_threshold": 0.08,
}


class SportsVolatilityStrategy(BaseStrategy):
    """Trades sports events that resolve within days, targeting
    high-volatility and high-volume markets for intra-day frequency.

    Signal logic:
    - Filter to sports-tagged events resolving within N days
    - Track rolling price windows to compute volatility
    - Buy when price dips below recent mean (mean reversion)
    - Sell when profit target or stop-loss is hit
    """

    name = "sports_volatility"
    description = "Sports events with high volatility, resolving within days"

    def __init__(self) -> None:
        self.params: dict = {}
        self._price_history: dict[str, list[float]] = defaultdict(list)

    def configure(self, config: PolyclawConfig) -> None:
        """Load strategy params from config."""
        self.params = {**DEFAULT_CONFIG}
        strategy_cfg = config.strategies.get("sports_volatility", {})
        self.params.update(strategy_cfg)
        logger.info("SportsVolatilityStrategy configured: %s", self.params)

    def _passes_filters(self, context: MarketContext) -> bool:
        """Check if this market qualifies for this strategy."""
        event = context.event
        params = self.params

        # Tag filter
        allowed_tags = [t.lower() for t in params.get("tags", [])]
        if allowed_tags:
            event_tags = [t.lower() for t in event.tags]
            if not any(t in allowed_tags for t in event_tags):
                return False

        # Time to resolution
        max_days = params.get("max_days_to_resolution", 7)
        if context.time_to_resolution is not None:
            if context.time_to_resolution.total_seconds() > max_days * 86400:
                return False
            if context.time_to_resolution.total_seconds() < 0:
                return False  # Already past end date
        elif event.end_date:
            try:
                end = datetime.fromisoformat(event.end_date.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                remaining = end - now
                if remaining.total_seconds() > max_days * 86400:
                    return False
                if remaining.total_seconds() < 0:
                    return False
            except (ValueError, TypeError):
                pass

        # Volume filter
        min_vol = params.get("min_volume_24hr", 5000)
        if context.volume_24hr < min_vol and event.volume_24hr < min_vol:
            return False

        # Spread filter
        max_spread = params.get("max_spread", 0.06)
        if context.spread > max_spread and context.spread > 0:
            return False

        return True

    def _update_price_history(self, token_id: str, price: float) -> None:
        """Add a price to the rolling window."""
        window_size = self.params.get("price_window_size", 20)
        history = self._price_history[token_id]
        history.append(price)
        if len(history) > window_size:
            self._price_history[token_id] = history[-window_size:]

    def _get_volatility(self, token_id: str) -> float:
        """Compute coefficient of variation from price history."""
        history = self._price_history.get(token_id, [])
        if len(history) < 3:
            return 0.0
        mean = statistics.mean(history)
        if mean == 0:
            return 0.0
        return statistics.stdev(history) / mean

    def evaluate(self, context: MarketContext) -> TradeSignal | None:
        """Evaluate market for a potential trade signal."""
        if not self._passes_filters(context):
            return None

        market = context.market
        midpoint = context.midpoint

        if midpoint <= 0.01 or midpoint >= 0.99:
            return None  # Too extreme, skip

        token_id = market.token_id_yes
        self._update_price_history(token_id, midpoint)

        volatility = self._get_volatility(token_id)
        min_vol = self.params.get("min_volatility", 0.03)

        if volatility < min_vol:
            return None

        history = self._price_history.get(token_id, [])
        if len(history) < 3:
            return None

        mean_price = statistics.mean(history)
        threshold = self.params.get("mean_reversion_threshold", 0.08)

        # Mean reversion: buy when price dips below mean
        if midpoint < mean_price * (1 - threshold):
            # Calculate position size based on confidence
            confidence = min(0.9, 0.6 + volatility)
            size = min(20.0, max(5.0, 10.0 * confidence))

            return TradeSignal(
                market_id=market.condition_id,
                token_id=token_id,
                side="BUY",
                outcome="Yes",
                price=midpoint,
                size=size,
                confidence=confidence,
                reasoning=(
                    f"Mean reversion: price {midpoint:.4f} < mean {mean_price:.4f} "
                    f"(vol={volatility:.3f}, spread={context.spread:.4f})"
                ),
                strategy=self.name,
                market_title=context.event.title,
                neg_risk=market.neg_risk,
            )

        return None

    def should_close(self, position: Position, context: MarketContext) -> TradeSignal | None:
        """Check if a position should be closed (take-profit or stop-loss)."""
        if context.midpoint <= 0:
            return None

        take_profit = self.params.get("take_profit_pct", 0.10)
        stop_loss = self.params.get("stop_loss_pct", 0.15)

        pnl_pct = (context.midpoint - position.entry_price) / position.entry_price

        reason = None
        if pnl_pct >= take_profit:
            reason = f"Take profit: {pnl_pct:.1%} >= {take_profit:.1%}"
        elif pnl_pct <= -stop_loss:
            reason = f"Stop loss: {pnl_pct:.1%} <= -{stop_loss:.1%}"

        # Also close if market is about to resolve (within 1 hour)
        if context.time_to_resolution and context.time_to_resolution < timedelta(hours=1):
            reason = f"Market resolving soon ({context.time_to_resolution})"

        if reason:
            return TradeSignal(
                market_id=position.market_id,
                token_id=position.token_id,
                side="SELL",
                outcome=position.outcome,
                price=context.midpoint,
                size=position.size,
                confidence=0.9,
                reasoning=reason,
                strategy=self.name,
                market_title=context.event.title,
            )

        return None

    # ── Scan candidates ───────────────────────────────────────────

    def scan_candidates(self, contexts: list) -> list[dict]:
        """Score and filter markets for the scan phase.

        Returns a list of candidate dicts sorted by score (desc).
        """
        candidates = []
        for ctx in contexts:
            if not self._passes_filters(ctx):
                continue

            market = ctx.market
            midpoint = ctx.midpoint
            if midpoint <= 0.01 or midpoint >= 0.99:
                continue

            token_id = market.token_id_yes

            # Compute a simple score: combine volatility potential + volume + spread tightness
            vol_score = 0.0
            history = self._price_history.get(token_id, [])
            if len(history) >= 3:
                import statistics
                mean = statistics.mean(history)
                vol_score = statistics.stdev(history) / mean if mean > 0 else 0.0

            spread = ctx.spread
            spread_score = max(0, 1 - spread / 0.10)  # tighter spread = higher score
            volume_score = min(1.0, (ctx.volume_24hr or ctx.event.volume_24hr) / 100000)

            # Composite score 0-100
            score = round(
                (vol_score * 40 + spread_score * 30 + volume_score * 30), 1
            )

            # Time to resolution
            ttr_hrs = None
            if ctx.time_to_resolution is not None:
                ttr_hrs = round(ctx.time_to_resolution.total_seconds() / 3600, 1)

            reasoning_parts = []
            if vol_score > 0:
                reasoning_parts.append(f"vol={vol_score:.3f}")
            reasoning_parts.append(f"spread={spread:.4f}")
            reasoning_parts.append(f"vol24h=${ctx.volume_24hr or ctx.event.volume_24hr:,.0f}")
            if ttr_hrs is not None:
                reasoning_parts.append(f"resolves in {ttr_hrs:.0f}h")

            candidates.append({
                "event_id": ctx.event.id,
                "event_title": ctx.event.title,
                "event_slug": ctx.event.slug,
                "polymarket_url": f"https://polymarket.com/event/{ctx.event.slug}" if ctx.event.slug else "",
                "market_id": market.condition_id,
                "question": market.question,
                "token_id": token_id,
                "midpoint": round(midpoint, 4),
                "spread": round(spread, 4),
                "volume_24hr": ctx.volume_24hr or ctx.event.volume_24hr,
                "time_to_resolution_hrs": ttr_hrs,
                "end_date": market.end_date or ctx.event.end_date,
                "tags": ctx.event.tags,
                "score": score,
                "reasoning": ", ".join(reasoning_parts),
            })

        # Sort by score descending
        candidates.sort(key=lambda c: c["score"], reverse=True)
        return candidates
