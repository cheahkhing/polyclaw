"""Output formatting helpers for Polyclaw CLI."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table

from polyclaw.models import (
    EvaluationReport,
    PolymarketEvent,
    Position,
    PortfolioSnapshot,
    TradeSignal,
)

console = Console()


def format_markets_table(events: list[PolymarketEvent]) -> Table:
    """Create a rich table showing events with their top-level stats."""
    table = Table(title="Active Markets", show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Title", min_width=30)
    table.add_column("Slug", style="cyan")
    table.add_column("Markets", justify="right")
    table.add_column("Vol 24h", justify="right", style="green")
    table.add_column("Liquidity", justify="right", style="yellow")

    for i, ev in enumerate(events, 1):
        table.add_row(
            str(i),
            ev.title[:60],
            ev.slug[:30],
            str(len(ev.markets)),
            f"${ev.volume_24hr:,.0f}",
            f"${ev.liquidity:,.0f}",
        )
    return table


def format_market_detail(event: PolymarketEvent) -> Table:
    """Detailed view of a single event and its markets."""
    table = Table(title=event.title, show_lines=True)
    table.add_column("Question", min_width=30)
    table.add_column("Yes", justify="right", style="green")
    table.add_column("No", justify="right", style="red")
    table.add_column("Tick", justify="right")
    table.add_column("Neg Risk", justify="center")
    table.add_column("Condition ID", style="dim", max_width=20)

    for m in event.markets:
        yes_price = m.outcome_prices.get("Yes", 0.0)
        no_price = m.outcome_prices.get("No", 0.0)
        table.add_row(
            m.question[:50],
            f"${yes_price:.4f}",
            f"${no_price:.4f}",
            m.tick_size,
            "✓" if m.neg_risk else "",
            m.condition_id[:18] + "…",
        )
    return table


def format_signals_table(signals: list[TradeSignal]) -> Table:
    """Display trade signals from strategies."""
    table = Table(title="Trade Signals", show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Strategy", style="magenta")
    table.add_column("Market", min_width=25)
    table.add_column("Side", justify="center")
    table.add_column("Outcome", justify="center")
    table.add_column("Price", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Conf", justify="right", style="yellow")
    table.add_column("Reasoning", max_width=40)

    for i, s in enumerate(signals, 1):
        side_style = "green" if s.side == "BUY" else "red"
        table.add_row(
            str(i),
            s.strategy,
            s.market_title[:25] if s.market_title else s.market_id[:25],
            f"[{side_style}]{s.side}[/{side_style}]",
            s.outcome,
            f"${s.price:.4f}",
            f"${s.size:.2f}",
            f"{s.confidence:.0%}",
            s.reasoning[:40],
        )
    return table


def format_positions_table(positions: list[Position]) -> Table:
    """Display open positions."""
    table = Table(title="Open Positions", show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Market ID", max_width=20)
    table.add_column("Outcome", justify="center")
    table.add_column("Entry", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Unr. P&L", justify="right")
    table.add_column("Strategy", style="magenta")
    table.add_column("Opened", style="dim")

    for i, p in enumerate(positions, 1):
        pnl_style = "green" if p.unrealized_pnl >= 0 else "red"
        table.add_row(
            str(i),
            p.market_id[:18] + "…",
            p.outcome,
            f"${p.entry_price:.4f}",
            f"${p.current_price:.4f}",
            f"${p.size:.2f}",
            f"[{pnl_style}]${p.unrealized_pnl:+.2f}[/{pnl_style}]",
            p.strategy,
            p.opened_at[:10] if p.opened_at else "",
        )
    return table


def format_evaluation_report(report: EvaluationReport) -> str:
    """Format an evaluation report as a human-readable string."""
    lines = [
        "═══ Polyclaw P&L Report ═══",
        f"Portfolio Balance:  ${report.portfolio_balance:,.2f}",
        f"Total P&L:          ${report.total_pnl:+,.2f}",
        f"Unrealized P&L:     ${report.unrealized_pnl:+,.2f}",
        f"Win Rate:           {report.win_rate:.1%}",
        f"Avg Return/Trade:   ${report.avg_return:+,.2f}",
        f"Max Drawdown:       ${report.max_drawdown:,.2f}",
        f"Total Trades:       {report.total_trades}",
        f"Open Positions:     {report.open_positions}",
        "",
        "─── Strategy Breakdown ───",
    ]
    for name, stats in report.strategy_breakdown.items():
        lines.append(
            f"  {name:20s}  "
            f"WR={stats.get('win_rate', 0):.0%}  "
            f"P&L=${stats.get('pnl', 0):+,.2f}  "
            f"Trades={stats.get('trades', 0)}"
        )
    return "\n".join(lines)


def format_portfolio_status(snapshot: PortfolioSnapshot) -> str:
    """Format a portfolio snapshot as a human-readable string."""
    return (
        f"Mode: {snapshot.mode.upper()}  |  "
        f"Balance: ${snapshot.total_balance:,.2f}  |  "
        f"Unrealized: ${snapshot.unrealized_pnl:+,.2f}  |  "
        f"Realized: ${snapshot.realized_pnl:+,.2f}  |  "
        f"Open: {snapshot.open_positions}  |  "
        f"Trades: {snapshot.total_trades}"
    )
