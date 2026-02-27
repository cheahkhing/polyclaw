"""Data models for Polyclaw."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Market / Event models (from Gamma API)
# ---------------------------------------------------------------------------

@dataclass
class PolymarketMarket:
    """A single binary-outcome market on Polymarket."""

    condition_id: str
    question: str
    token_id_yes: str
    token_id_no: str
    tick_size: str = "0.01"
    neg_risk: bool = False
    enable_order_book: bool = True
    outcome_prices: dict[str, float] = field(default_factory=dict)
    description: str = ""
    slug: str = ""
    end_date: str | None = None
    closed: bool = False
    resolution: str | None = None  # "Yes" / "No" when resolved

    @property
    def implied_probability(self) -> float:
        """Implied probability of Yes from the market price."""
        return self.outcome_prices.get("Yes", 0.0)


@dataclass
class PolymarketEvent:
    """A container grouping one or more related markets."""

    id: str
    slug: str
    title: str
    markets: list[PolymarketMarket] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    volume_24hr: float = 0.0
    liquidity: float = 0.0
    start_date: str | None = None
    end_date: str | None = None
    closed: bool = False


# ---------------------------------------------------------------------------
# Trading models
# ---------------------------------------------------------------------------

@dataclass
class TradeSignal:
    """A signal produced by a strategy recommending a trade."""

    market_id: str  # condition_id
    token_id: str
    side: Literal["BUY", "SELL"]
    outcome: Literal["Yes", "No"]
    price: float
    size: float
    confidence: float  # 0.0 – 1.0
    reasoning: str
    order_type: Literal["GTC", "FOK", "FAK"] = "GTC"
    strategy: str = ""
    market_title: str = ""
    neg_risk: bool = False


@dataclass
class MarketContext:
    """Contextual data supplied to strategies for evaluation."""

    event: PolymarketEvent
    market: PolymarketMarket
    midpoint: float = 0.0
    spread: float = 0.0
    orderbook_depth: dict = field(default_factory=dict)
    volume_24hr: float = 0.0
    time_to_resolution: timedelta | None = None


@dataclass
class Position:
    """An open or closed position tracked by the ledger."""

    id: int | None = None
    market_id: str = ""
    token_id: str = ""
    outcome: str = ""
    entry_price: float = 0.0
    size: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    strategy: str = ""
    opened_at: str = ""
    closed_at: str | None = None
    exit_price: float | None = None
    realized_pnl: float | None = None


@dataclass
class MockTradeResult:
    """Result of a simulated (mock) trade execution."""

    trade_id: int | None = None
    signal: TradeSignal | None = None
    fill_price: float = 0.0
    slippage: float = 0.0
    balance_after: float = 0.0
    timestamp: str = ""
    success: bool = True
    error: str | None = None


@dataclass
class LiveTradeResult:
    """Result of a real trade execution via the CLOB API."""

    order_id: str = ""
    signal: TradeSignal | None = None
    status: str = ""  # pending, filled, cancelled, failed
    fill_price: float | None = None
    timestamp: str = ""
    success: bool = True
    error: str | None = None


# ---------------------------------------------------------------------------
# Portfolio / Reporting models
# ---------------------------------------------------------------------------

@dataclass
class PortfolioSnapshot:
    """A point-in-time snapshot of the portfolio state."""

    timestamp: str = ""
    mode: str = "mock"
    total_balance: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    open_positions: int = 0
    total_trades: int = 0


@dataclass
class EvaluationReport:
    """P&L and strategy performance report."""

    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_return: float = 0.0
    max_drawdown: float = 0.0
    total_trades: int = 0
    open_positions: int = 0
    unrealized_pnl: float = 0.0
    strategy_breakdown: dict[str, dict] = field(default_factory=dict)
    portfolio_balance: float = 0.0


# ---------------------------------------------------------------------------
# Simulation models
# ---------------------------------------------------------------------------

@dataclass
class SimRun:
    """Metadata for a single simulation run."""

    run_id: str = ""
    strategy: str = ""
    started_at: str = ""
    ended_at: str | None = None
    config_snapshot: str = ""  # JSON dump of full config at start
    status: str = "running"    # running | paused | completed | aborted
    notes: str = ""


@dataclass
class RiskVerdict:
    """Result of a pre-trade risk check."""

    approved: bool = True
    reason: str = ""
    signal: TradeSignal | None = None


@dataclass
class SimEvent:
    """An event pushed through the EventBus to the dashboard."""

    type: str = ""        # tick, trade_executed, price_update, etc.
    timestamp: str = ""   # ISO 8601
    data: dict[str, Any] = field(default_factory=dict)
