"""Strategy abstraction — base class and registry for pluggable strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from polyclaw.config import PolyclawConfig
from polyclaw.models import MarketContext, Position, TradeSignal
from polyclaw.utils.logging import get_logger

logger = get_logger("strategy")


class BaseStrategy(ABC):
    """All simulation strategies implement this interface."""

    name: str = ""
    description: str = ""

    @abstractmethod
    def configure(self, config: PolyclawConfig) -> None:
        """One-time setup (filters, thresholds, etc.)."""

    @abstractmethod
    def evaluate(self, context: MarketContext) -> TradeSignal | None:
        """Given market context, return a trade signal or None to skip."""

    @abstractmethod
    def should_close(self, position: Position, context: MarketContext) -> TradeSignal | None:
        """Decide whether to close/reduce an existing position."""

    def scan_candidates(
        self,
        contexts: list[MarketContext],
    ) -> list[dict]:
        """Filter and score markets, returning a list of candidate dicts.

        Default implementation calls _passes_filters if available.
        Strategies should override for richer scoring.

        Each dict should contain at least:
            event_id, event_title, market_id, question, token_id,
            midpoint, spread, volume_24hr, time_to_resolution_hrs,
            end_date, tags, score, reasoning
        """
        return []

    def __repr__(self) -> str:
        return f"<Strategy: {self.name}>"


class StrategyRegistry:
    """Registry for discovering and managing strategies."""

    def __init__(self) -> None:
        self._strategies: dict[str, BaseStrategy] = {}

    def register(self, strategy: BaseStrategy) -> None:
        """Register a strategy instance."""
        self._strategies[strategy.name] = strategy
        logger.info("Registered strategy: %s", strategy.name)

    def get(self, name: str) -> BaseStrategy:
        """Get a strategy by name."""
        if name not in self._strategies:
            raise KeyError(f"Strategy '{name}' not found. Available: {self.list_all()}")
        return self._strategies[name]

    def list_all(self) -> list[str]:
        """List all registered strategy names."""
        return list(self._strategies.keys())

    def get_all(self) -> list[BaseStrategy]:
        """Return all registered strategy instances."""
        return list(self._strategies.values())
