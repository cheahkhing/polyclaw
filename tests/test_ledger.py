"""Tests for Trade Ledger (SQLite)."""

import os
import tempfile
from datetime import datetime, timezone

import pytest

from polyclaw.ledger import TradeLedger
from polyclaw.models import PortfolioSnapshot, Position, TradeSignal


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def ledger(db_path):
    led = TradeLedger(db_path=db_path)
    yield led
    led.close()


def _make_signal(strategy="mispricing_hunter", market_id="m1") -> TradeSignal:
    return TradeSignal(
        market_id=market_id,
        token_id="y1",
        side="BUY",
        outcome="Yes",
        price=0.50,
        size=10.0,
        confidence=0.8,
        strategy=strategy,
        reasoning="test signal",
        market_title="Test Market?",
    )


class TestTradeRecording:
    def test_record_and_retrieve(self, ledger):
        signal = _make_signal()
        trade_id = ledger.record_trade(signal, fill_price=0.505)
        assert trade_id > 0
        trades = ledger.get_trades()
        assert len(trades) == 1
        assert trades[0]["strategy"] == "mispricing_hunter"
        assert trades[0]["fill_price"] == pytest.approx(0.505)

    def test_multiple_trades(self, ledger):
        for i in range(5):
            signal = _make_signal(market_id=f"m{i}")
            ledger.record_trade(signal)
        trades = ledger.get_trades()
        assert len(trades) == 5

    def test_filter_by_strategy(self, ledger):
        for strategy in ["alpha", "alpha", "beta"]:
            signal = _make_signal(strategy=strategy)
            ledger.record_trade(signal)
        alpha_trades = ledger.get_trades(strategy="alpha")
        assert len(alpha_trades) == 2
        beta_trades = ledger.get_trades(strategy="beta")
        assert len(beta_trades) == 1


class TestPositions:
    def test_save_and_get_positions(self, ledger):
        pos = Position(
            market_id="m1",
            token_id="y1",
            outcome="Yes",
            entry_price=0.50,
            size=10.0,
            current_price=0.50,
            unrealized_pnl=0.0,
            strategy="test",
            opened_at=datetime.now(timezone.utc).isoformat(),
        )
        pos_id = ledger.save_position(pos)
        assert pos_id > 0
        positions = ledger.get_open_positions()
        assert len(positions) == 1
        assert positions[0].token_id == "y1"

    def test_update_position(self, ledger):
        pos = Position(
            market_id="m1",
            token_id="y1",
            outcome="Yes",
            entry_price=0.50,
            size=10.0,
            current_price=0.50,
            unrealized_pnl=0.0,
            strategy="test",
            opened_at=datetime.now(timezone.utc).isoformat(),
        )
        pos_id = ledger.save_position(pos)
        # Update with new size
        pos.id = pos_id
        pos.size = 15.0
        pos.current_price = 0.55
        pos.unrealized_pnl = 0.75
        ledger.save_position(pos)
        positions = ledger.get_open_positions()
        assert len(positions) == 1
        assert positions[0].size == pytest.approx(15.0)


class TestPortfolioSnapshots:
    def test_record_snapshot(self, ledger):
        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            mode="mock",
            total_balance=1050.0,
            unrealized_pnl=50.0,
            realized_pnl=200.0,
            open_positions=3,
            total_trades=10,
        )
        snap_id = ledger.save_snapshot(snapshot)
        assert snap_id > 0
        latest = ledger.get_latest_snapshot(mode="mock")
        assert latest is not None
        assert latest.total_balance == pytest.approx(1050.0)

    def test_multiple_snapshots(self, ledger):
        for i in range(3):
            snapshot = PortfolioSnapshot(
                timestamp=datetime.now(timezone.utc).isoformat(),
                mode="mock",
                total_balance=1000 + i * 50,
                unrealized_pnl=i * 50.0,
                realized_pnl=0.0,
                open_positions=i,
                total_trades=i * 2,
            )
            ledger.save_snapshot(snapshot)
        snapshots = ledger.get_snapshots(mode="mock")
        assert len(snapshots) == 3


class TestAggregateQueries:
    def test_total_trades(self, ledger):
        for i in range(4):
            ledger.record_trade(_make_signal(market_id=f"m{i}"))
        assert ledger.get_total_trades(mode="mock") == 4

    def test_realized_pnl_initially_zero(self, ledger):
        ledger.record_trade(_make_signal())
        pnl = ledger.get_realized_pnl(mode="mock")
        assert pnl == pytest.approx(0.0)

    def test_today_trade_count(self, ledger):
        for _ in range(3):
            ledger.record_trade(_make_signal())
        assert ledger.get_today_trade_count(mode="mock") == 3
