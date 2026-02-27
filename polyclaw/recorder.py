"""Price Recorder & Replayer — records live price data for backtesting."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Iterator

from polyclaw.models import PolymarketEvent
from polyclaw.utils.logging import get_logger

logger = get_logger("recorder")

RECORDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_ticks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id    TEXT NOT NULL,
    midpoint    REAL NOT NULL,
    bid         REAL,
    ask         REAL,
    spread      REAL,
    timestamp   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_price_ticks_token
    ON price_ticks(token_id, timestamp);

CREATE TABLE IF NOT EXISTS event_metadata (
    condition_id TEXT PRIMARY KEY,
    event_id     TEXT,
    slug         TEXT,
    title        TEXT,
    question     TEXT,
    tags         TEXT,
    end_date     TEXT,
    neg_risk     INTEGER,
    token_id_yes TEXT,
    token_id_no  TEXT,
    recorded_at  TEXT
);

CREATE TABLE IF NOT EXISTS scan_sessions (
    scan_id     TEXT PRIMARY KEY,
    strategy    TEXT NOT NULL,
    candidates  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


class PriceRecorder:
    """Records price snapshots to SQLite for later replay."""

    def __init__(self, db_path: str = "./data/price_history.db"):
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(RECORDER_SCHEMA)
        self._conn.commit()
        logger.info("PriceRecorder initialised at %s", db_path)

    def record_tick(
        self,
        token_id: str,
        midpoint: float,
        bid: float = 0.0,
        ask: float = 0.0,
        spread: float = 0.0,
        timestamp: str | None = None,
    ) -> None:
        """Store one price observation."""
        ts = timestamp or datetime.utcnow().isoformat()
        self._conn.execute(
            "INSERT INTO price_ticks (token_id, midpoint, bid, ask, spread, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (token_id, midpoint, bid, ask, spread, ts),
        )
        self._conn.commit()

    def record_event_metadata(self, event: PolymarketEvent) -> None:
        """Store event/market metadata for context reconstruction."""
        import json
        for market in event.markets:
            self._conn.execute(
                """INSERT OR REPLACE INTO event_metadata
                (condition_id, event_id, slug, title, question, tags,
                 end_date, neg_risk, token_id_yes, token_id_no, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    market.condition_id,
                    event.id,
                    event.slug,
                    event.title,
                    market.question,
                    json.dumps(event.tags),
                    event.end_date,
                    1 if market.neg_risk else 0,
                    market.token_id_yes,
                    market.token_id_no,
                    datetime.utcnow().isoformat(),
                ),
            )
        self._conn.commit()

    def record_scan_session(
        self, scan_id: str, strategy: str, candidates: list[dict]
    ) -> None:
        """Persist a scan session with its candidate results."""
        import json
        self._conn.execute(
            "INSERT OR REPLACE INTO scan_sessions (scan_id, strategy, candidates, created_at) "
            "VALUES (?, ?, ?, ?)",
            (scan_id, strategy, json.dumps(candidates), datetime.utcnow().isoformat()),
        )
        self._conn.commit()
        logger.debug("Recorded scan session %s with %d candidates", scan_id, len(candidates))

    def get_scan_sessions(self, limit: int = 20) -> list[dict]:
        """Return recent scan sessions."""
        import json
        rows = self._conn.execute(
            "SELECT * FROM scan_sessions ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["candidates"] = json.loads(d["candidates"])
            results.append(d)
        return results

    def close(self) -> None:
        self._conn.close()


class PriceReplayer:
    """Replays recorded price ticks as if they were live."""

    def __init__(self, db_path: str = "./data/price_history.db"):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row

    def get_ticks(
        self, token_id: str, start: str | None = None, end: str | None = None
    ) -> list[dict]:
        """Get price ticks for a token in a time range."""
        query = "SELECT * FROM price_ticks WHERE token_id = ?"
        params: list = [token_id]
        if start:
            query += " AND timestamp >= ?"
            params.append(start)
        if end:
            query += " AND timestamp <= ?"
            params.append(end)
        query += " ORDER BY timestamp ASC"
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def iter_ticks(
        self, start: str | None = None, end: str | None = None
    ) -> Iterator[tuple[str, str, float]]:
        """Yields (timestamp, token_id, midpoint) in chronological order."""
        query = "SELECT timestamp, token_id, midpoint FROM price_ticks WHERE 1=1"
        params: list = []
        if start:
            query += " AND timestamp >= ?"
            params.append(start)
        if end:
            query += " AND timestamp <= ?"
            params.append(end)
        query += " ORDER BY timestamp ASC"

        for row in self._conn.execute(query, params):
            yield (row["timestamp"], row["token_id"], row["midpoint"])

    def close(self) -> None:
        self._conn.close()
