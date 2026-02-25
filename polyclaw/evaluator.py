"""P&L Evaluator — scores strategy performance from the ledger."""

from __future__ import annotations

import json
from datetime import datetime

from polyclaw.config import PolyclawConfig
from polyclaw.ledger import TradeLedger
from polyclaw.models import EvaluationReport, PortfolioSnapshot
from polyclaw.pricer import PriceEngine
from polyclaw.utils.logging import get_logger

logger = get_logger("evaluator")


class Evaluator:
    """Scores strategy performance from the trade ledger."""

    def __init__(
        self,
        config: PolyclawConfig,
        ledger: TradeLedger,
        pricer: PriceEngine | None = None,
    ):
        self.config = config
        self.ledger = ledger
        self.pricer = pricer

    def generate_report(self, mode: str | None = None) -> EvaluationReport:
        """Generate a comprehensive P&L and strategy performance report."""
        mode = mode or self.config.mode
        trades = self.ledger.get_trades(mode=mode, limit=10000)
        strategy_stats = self.ledger.get_strategy_stats(mode=mode)
        open_positions = self.ledger.get_open_positions()

        # -- Win rate and P&L --
        resolved_trades = [t for t in trades if t.get("pnl") is not None]
        profitable = [t for t in resolved_trades if (t.get("pnl") or 0) > 0]

        total_pnl = sum(t.get("pnl", 0) or 0 for t in resolved_trades)
        win_rate = len(profitable) / len(resolved_trades) if resolved_trades else 0.0
        avg_return = total_pnl / len(resolved_trades) if resolved_trades else 0.0

        # -- Unrealized P&L --
        unrealized_pnl = 0.0
        if self.pricer and open_positions:
            for pos in open_positions:
                try:
                    current = self.pricer.get_midpoint(pos.token_id)
                    if current > 0:
                        pos.current_price = current
                        pos.unrealized_pnl = (current - pos.entry_price) * pos.size
                        unrealized_pnl += pos.unrealized_pnl
                except Exception:
                    pass

        # -- Max drawdown --
        max_drawdown = self._calculate_max_drawdown(trades)

        # -- Portfolio balance --
        snapshot = self.ledger.get_latest_snapshot(mode=mode)
        portfolio_balance = snapshot.total_balance if snapshot else self.config.mock.starting_balance

        report = EvaluationReport(
            win_rate=win_rate,
            total_pnl=total_pnl,
            avg_return=avg_return,
            max_drawdown=max_drawdown,
            total_trades=len(trades),
            open_positions=len(open_positions),
            unrealized_pnl=unrealized_pnl,
            strategy_breakdown=strategy_stats,
            portfolio_balance=portfolio_balance,
        )

        return report

    def _calculate_max_drawdown(self, trades: list[dict]) -> float:
        """Calculate maximum drawdown from trade history."""
        if not trades:
            return 0.0

        # Sort chronologically
        sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", ""))

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for t in sorted_trades:
            pnl = t.get("pnl") or 0
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            drawdown = peak - cumulative
            if drawdown > max_dd:
                max_dd = drawdown

        return max_dd

    def take_snapshot(
        self,
        balance: float,
        mode: str | None = None,
    ) -> PortfolioSnapshot:
        """Create and save a portfolio snapshot."""
        mode = mode or self.config.mode
        open_positions = self.ledger.get_open_positions()
        total_trades = self.ledger.get_total_trades(mode=mode)
        realized_pnl = self.ledger.get_realized_pnl(mode=mode)

        unrealized_pnl = 0.0
        if self.pricer:
            for pos in open_positions:
                try:
                    current = self.pricer.get_midpoint(pos.token_id)
                    if current > 0:
                        unrealized_pnl += (current - pos.entry_price) * pos.size
                except Exception:
                    pass

        snapshot = PortfolioSnapshot(
            timestamp=datetime.utcnow().isoformat(),
            mode=mode,
            total_balance=balance,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            open_positions=len(open_positions),
            total_trades=total_trades,
        )

        self.ledger.save_snapshot(snapshot)
        return snapshot

    def to_json(self, report: EvaluationReport) -> str:
        """Serialize a report to JSON."""
        return json.dumps(
            {
                "win_rate": report.win_rate,
                "total_pnl": report.total_pnl,
                "avg_return": report.avg_return,
                "max_drawdown": report.max_drawdown,
                "total_trades": report.total_trades,
                "open_positions": report.open_positions,
                "unrealized_pnl": report.unrealized_pnl,
                "portfolio_balance": report.portfolio_balance,
                "strategy_breakdown": report.strategy_breakdown,
            },
            indent=2,
        )
