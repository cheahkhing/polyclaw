"""Exporter — export simulation results to CSV, JSON, Markdown."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from polyclaw.ledger import TradeLedger
from polyclaw.utils.logging import get_logger

logger = get_logger("exporter")


class SimExporter:
    """Export simulation run data in various formats."""

    def __init__(self, ledger: TradeLedger):
        self.ledger = ledger

    def to_csv(self, run_id: str | None = None, output_path: str | None = None) -> str:
        """Export trades to CSV. Returns CSV string, optionally writes to file."""
        trades = self.ledger.get_trades(mode="mock", limit=10000)

        output = io.StringIO()
        if trades:
            writer = csv.DictWriter(output, fieldnames=trades[0].keys())
            writer.writeheader()
            writer.writerows(trades)

        csv_str = output.getvalue()

        if output_path:
            with open(output_path, "w", encoding="utf-8", newline="") as f:
                f.write(csv_str)
            logger.info("Exported %d trades to %s", len(trades), output_path)

        return csv_str

    def to_json(self, run_id: str | None = None, output_path: str | None = None) -> str:
        """Export trades to JSON."""
        trades = self.ledger.get_trades(mode="mock", limit=10000)

        json_str = json.dumps(trades, indent=2, default=str)

        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(json_str)
            logger.info("Exported %d trades to %s", len(trades), output_path)

        return json_str

    def summary_markdown(self, run_id: str | None = None) -> str:
        """Generate a markdown summary of a simulation run."""
        trades = self.ledger.get_trades(mode="mock", limit=10000)
        stats = self.ledger.get_strategy_stats(mode="mock")

        total = len(trades)
        filled = [t for t in trades if t.get("status") == "filled"]
        pnl_trades = [t for t in trades if t.get("pnl") is not None]
        total_pnl = sum(t.get("pnl", 0) or 0 for t in pnl_trades)
        wins = [t for t in pnl_trades if (t.get("pnl") or 0) > 0]
        win_rate = len(wins) / len(pnl_trades) if pnl_trades else 0

        lines = [
            "# Simulation Run Report",
            "",
            f"- **Total Trades:** {total}",
            f"- **Filled:** {len(filled)}",
            f"- **Total P&L:** ${total_pnl:+.2f}",
            f"- **Win Rate:** {win_rate:.1%}",
            "",
            "## Strategy Breakdown",
            "",
            "| Strategy | Trades | Wins | Win Rate | P&L |",
            "|----------|--------|------|----------|-----|",
        ]

        for name, s in stats.items():
            lines.append(
                f"| {name} | {s['trades']} | {s['wins']} | "
                f"{s['win_rate']:.1%} | ${s['pnl']:+.2f} |"
            )

        return "\n".join(lines)
