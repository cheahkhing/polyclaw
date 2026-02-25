"""Trade Ledger — SQLite-based persistence for all trade records."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from polyclaw.config import PolyclawConfig
from polyclaw.models import (
    MockTradeResult,
    PortfolioSnapshot,
    Position,
    TradeSignal,
)
from polyclaw.utils.logging import get_logger

logger = get_logger("ledger")

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL,
    mode          TEXT NOT NULL,
    strategy      TEXT NOT NULL,
    market_id     TEXT NOT NULL,
    market_title  TEXT,
    token_id      TEXT NOT NULL,
    side          TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    price         REAL NOT NULL,
    size          REAL NOT NULL,
    confidence    REAL,
    reasoning     TEXT,
    order_type    TEXT,
    fill_price    REAL,
    status        TEXT NOT NULL,
    pnl           REAL,
    resolved_at   TEXT,
    resolution    TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id     TEXT NOT NULL,
    token_id      TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    entry_price   REAL NOT NULL,
    size          REAL NOT NULL,
    current_price REAL,
    unrealized_pnl REAL,
    strategy      TEXT NOT NULL,
    opened_at     TEXT NOT NULL,
    closed_at     TEXT,
    exit_price    REAL,
    realized_pnl  REAL
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL,
    mode          TEXT NOT NULL,
    total_balance REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    realized_pnl  REAL NOT NULL,
    open_positions INTEGER NOT NULL,
    total_trades   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market_id);
CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_positions_market ON positions(market_id);
"""


class TradeLedger:
    """SQLite-based trade journal for recording and querying trade history."""

    def __init__(self, config: PolyclawConfig | None = None, db_path: str | None = None):
        path = db_path or (config.database.path if config else "./data/polyclaw.db")
        self.db_path = path

        # Ensure directory exists
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        logger.info("Ledger initialised at %s", path)

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    def record_trade(
        self,
        signal: TradeSignal,
        mode: str = "mock",
        fill_price: float | None = None,
        status: str = "filled",
    ) -> int:
        """Insert a trade record and return the trade ID."""
        now = datetime.utcnow().isoformat()
        cursor = self._conn.execute(
            """
            INSERT INTO trades (
                timestamp, mode, strategy, market_id, market_title,
                token_id, side, outcome, price, size,
                confidence, reasoning, order_type, fill_price, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                mode,
                signal.strategy,
                signal.market_id,
                signal.market_title,
                signal.token_id,
                signal.side,
                signal.outcome,
                signal.price,
                signal.size,
                signal.confidence,
                signal.reasoning,
                signal.order_type,
                fill_price or signal.price,
                status,
            ),
        )
        self._conn.commit()
        trade_id = cursor.lastrowid or 0
        logger.debug("Recorded trade #%d", trade_id)
        return trade_id

    def record_mock_result(self, result: MockTradeResult) -> int:
        """Record a mock trade result."""
        if result.signal is None:
            return 0
        return self.record_trade(
            signal=result.signal,
            mode="mock",
            fill_price=result.fill_price,
            status="filled" if result.success else "failed",
        )

    def get_trades(
        self,
        mode: str | None = None,
        strategy: str | None = None,
        market_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query trades with optional filters."""
        query = "SELECT * FROM trades WHERE 1=1"
        params: list[Any] = []

        if mode:
            query += " AND mode = ?"
            params.append(mode)
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        if market_id:
            query += " AND market_id = ?"
            params.append(market_id)
        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def update_trade_resolution(
        self, market_id: str, resolution: str, pnl: float | None = None
    ) -> int:
        """Update trades when a market resolves."""
        now = datetime.utcnow().isoformat()
        cursor = self._conn.execute(
            """
            UPDATE trades
            SET resolution = ?, resolved_at = ?, pnl = COALESCE(?, pnl)
            WHERE market_id = ? AND resolution IS NULL
            """,
            (resolution, now, pnl, market_id),
        )
        self._conn.commit()
        return cursor.rowcount

    def get_today_trade_count(self, mode: str = "mock") -> int:
        """Count trades placed today (for daily trade limit enforcement)."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM trades WHERE mode = ? AND timestamp LIKE ?",
            (mode, f"{today}%"),
        ).fetchone()
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def save_position(self, position: Position) -> int:
        """Insert or update a position record."""
        if position.id:
            self._conn.execute(
                """
                UPDATE positions SET
                    current_price = ?, unrealized_pnl = ?, size = ?,
                    closed_at = ?, exit_price = ?, realized_pnl = ?
                WHERE id = ?
                """,
                (
                    position.current_price,
                    position.unrealized_pnl,
                    position.size,
                    position.closed_at,
                    position.exit_price,
                    position.realized_pnl,
                    position.id,
                ),
            )
            self._conn.commit()
            return position.id
        else:
            cursor = self._conn.execute(
                """
                INSERT INTO positions (
                    market_id, token_id, outcome, entry_price, size,
                    current_price, unrealized_pnl, strategy, opened_at,
                    closed_at, exit_price, realized_pnl
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position.market_id,
                    position.token_id,
                    position.outcome,
                    position.entry_price,
                    position.size,
                    position.current_price,
                    position.unrealized_pnl,
                    position.strategy,
                    position.opened_at,
                    position.closed_at,
                    position.exit_price,
                    position.realized_pnl,
                ),
            )
            self._conn.commit()
            return cursor.lastrowid or 0

    def get_open_positions(self) -> list[Position]:
        """Return all open positions (closed_at IS NULL)."""
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE closed_at IS NULL"
        ).fetchall()
        return [self._row_to_position(r) for r in rows]

    def get_all_positions(self) -> list[Position]:
        """Return all positions (open and closed)."""
        rows = self._conn.execute(
            "SELECT * FROM positions ORDER BY opened_at DESC"
        ).fetchall()
        return [self._row_to_position(r) for r in rows]

    def _row_to_position(self, row: sqlite3.Row) -> Position:
        return Position(
            id=row["id"],
            market_id=row["market_id"],
            token_id=row["token_id"],
            outcome=row["outcome"],
            entry_price=row["entry_price"],
            size=row["size"],
            current_price=row["current_price"] or 0.0,
            unrealized_pnl=row["unrealized_pnl"] or 0.0,
            strategy=row["strategy"],
            opened_at=row["opened_at"],
            closed_at=row["closed_at"],
            exit_price=row["exit_price"],
            realized_pnl=row["realized_pnl"],
        )

    # ------------------------------------------------------------------
    # Portfolio Snapshots
    # ------------------------------------------------------------------

    def save_snapshot(self, snapshot: PortfolioSnapshot) -> int:
        """Insert a portfolio snapshot."""
        cursor = self._conn.execute(
            """
            INSERT INTO portfolio_snapshots (
                timestamp, mode, total_balance, unrealized_pnl,
                realized_pnl, open_positions, total_trades
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.timestamp or datetime.utcnow().isoformat(),
                snapshot.mode,
                snapshot.total_balance,
                snapshot.unrealized_pnl,
                snapshot.realized_pnl,
                snapshot.open_positions,
                snapshot.total_trades,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def get_latest_snapshot(self, mode: str = "mock") -> PortfolioSnapshot | None:
        """Return the most recent portfolio snapshot."""
        row = self._conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE mode = ? ORDER BY timestamp DESC LIMIT 1",
            (mode,),
        ).fetchone()
        if not row:
            return None
        return PortfolioSnapshot(
            timestamp=row["timestamp"],
            mode=row["mode"],
            total_balance=row["total_balance"],
            unrealized_pnl=row["unrealized_pnl"],
            realized_pnl=row["realized_pnl"],
            open_positions=row["open_positions"],
            total_trades=row["total_trades"],
        )

    def get_snapshots(
        self, mode: str = "mock", limit: int = 100
    ) -> list[PortfolioSnapshot]:
        """Return recent portfolio snapshots."""
        rows = self._conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE mode = ? ORDER BY timestamp DESC LIMIT ?",
            (mode, limit),
        ).fetchall()
        return [
            PortfolioSnapshot(
                timestamp=r["timestamp"],
                mode=r["mode"],
                total_balance=r["total_balance"],
                unrealized_pnl=r["unrealized_pnl"],
                realized_pnl=r["realized_pnl"],
                open_positions=r["open_positions"],
                total_trades=r["total_trades"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Aggregate queries
    # ------------------------------------------------------------------

    def get_total_trades(self, mode: str | None = None) -> int:
        """Total number of trades."""
        if mode:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM trades WHERE mode = ?", (mode,)
            ).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) as cnt FROM trades").fetchone()
        return row["cnt"] if row else 0

    def get_realized_pnl(self, mode: str | None = None) -> float:
        """Sum of all realized P&L."""
        if mode:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) as total FROM trades WHERE mode = ? AND pnl IS NOT NULL",
                (mode,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) as total FROM trades WHERE pnl IS NOT NULL"
            ).fetchone()
        return float(row["total"]) if row else 0.0

    def get_strategy_stats(self, mode: str = "mock") -> dict[str, dict]:
        """Per-strategy trade count, win rate, and P&L."""
        rows = self._conn.execute(
            """
            SELECT
                strategy,
                COUNT(*) as trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl IS NOT NULL THEN 1 ELSE 0 END) as resolved,
                COALESCE(SUM(pnl), 0) as total_pnl
            FROM trades
            WHERE mode = ?
            GROUP BY strategy
            """,
            (mode,),
        ).fetchall()

        stats: dict[str, dict] = {}
        for r in rows:
            resolved = r["resolved"] or 0
            wins = r["wins"] or 0
            stats[r["strategy"]] = {
                "trades": r["trades"],
                "resolved": resolved,
                "wins": wins,
                "win_rate": wins / resolved if resolved > 0 else 0.0,
                "pnl": float(r["total_pnl"]),
            }
        return stats
