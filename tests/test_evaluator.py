"""Tests for P&L Evaluator."""

import os
import tempfile

import pytest

from polyclaw.config import PolyclawConfig
from polyclaw.evaluator import Evaluator
from polyclaw.ledger import TradeLedger
from polyclaw.models import TradeSignal


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def config():
    return PolyclawConfig()


@pytest.fixture
def ledger(db_path):
    led = TradeLedger(db_path=db_path)
    yield led
    led.close()


@pytest.fixture
def evaluator(config, ledger):
    return Evaluator(config=config, ledger=ledger)


def _make_signal(strategy="test", price=0.50) -> TradeSignal:
    return TradeSignal(
        market_id="m1",
        token_id="y1",
        side="BUY",
        outcome="Yes",
        price=price,
        size=10.0,
        confidence=0.8,
        strategy=strategy,
        reasoning="test trade",
        market_title="Test Market?",
    )


class TestReportWithNoTrades:
    def test_empty_report(self, evaluator):
        report = evaluator.generate_report()
        assert report.win_rate == 0.0
        assert report.total_pnl == 0.0
        assert report.total_trades == 0


class TestReportWithTrades:
    def test_records_trades(self, evaluator, ledger):
        for i in range(3):
            ledger.record_trade(_make_signal(strategy="alpha"))
        report = evaluator.generate_report()
        assert report.total_trades == 3

    def test_win_rate_with_resolved(self, evaluator, ledger):
        # Record trades and resolve them with pnl
        for _ in range(2):
            ledger.record_trade(_make_signal(), fill_price=0.50)
        # Manually update pnl on trades to simulate resolution
        ledger._conn.execute("UPDATE trades SET pnl = 10.0 WHERE id = 1")
        ledger._conn.execute("UPDATE trades SET pnl = -5.0 WHERE id = 2")
        ledger._conn.commit()

        report = evaluator.generate_report()
        assert report.win_rate == pytest.approx(0.5)
        assert report.total_pnl == pytest.approx(5.0)


class TestMaxDrawdown:
    def test_drawdown_from_trades(self, evaluator, ledger):
        # Insert trades with known P&L sequence
        prices = [0.50] * 6
        pnls = [10.0, 20.0, -15.0, -25.0, 5.0, 30.0]
        for i, (p, pnl) in enumerate(zip(prices, pnls)):
            tid = ledger.record_trade(_make_signal(price=p))
            ledger._conn.execute("UPDATE trades SET pnl = ? WHERE id = ?", (pnl, tid))
        ledger._conn.commit()

        dd = evaluator._calculate_max_drawdown(ledger.get_trades(limit=100))
        # Cumulative: 10, 30, 15, -10, -5, 25
        # Peak at 30, trough at -10 → drawdown = 40
        assert dd == pytest.approx(40.0)

    def test_no_drawdown_monotonic(self, evaluator, ledger):
        for pnl in [5.0, 10.0, 15.0]:
            tid = ledger.record_trade(_make_signal())
            ledger._conn.execute("UPDATE trades SET pnl = ? WHERE id = ?", (pnl, tid))
        ledger._conn.commit()

        dd = evaluator._calculate_max_drawdown(ledger.get_trades(limit=100))
        assert dd == pytest.approx(0.0)


class TestStrategyBreakdown:
    def test_breakdown_by_strategy(self, evaluator, ledger):
        # Two strategies with different results
        tid1 = ledger.record_trade(_make_signal(strategy="alpha"))
        ledger._conn.execute("UPDATE trades SET pnl = 10.0 WHERE id = ?", (tid1,))
        tid2 = ledger.record_trade(_make_signal(strategy="alpha"))
        ledger._conn.execute("UPDATE trades SET pnl = -5.0 WHERE id = ?", (tid2,))
        tid3 = ledger.record_trade(_make_signal(strategy="beta"))
        ledger._conn.execute("UPDATE trades SET pnl = 20.0 WHERE id = ?", (tid3,))
        ledger._conn.commit()

        report = evaluator.generate_report()
        breakdown = report.strategy_breakdown
        assert "alpha" in breakdown
        assert "beta" in breakdown
        assert breakdown["alpha"]["pnl"] == pytest.approx(5.0)
        assert breakdown["beta"]["pnl"] == pytest.approx(20.0)
        assert breakdown["alpha"]["trades"] == 2
        assert breakdown["beta"]["trades"] == 1


class TestSnapshot:
    def test_take_snapshot(self, evaluator, ledger):
        ledger.record_trade(_make_signal())
        snapshot = evaluator.take_snapshot(balance=950.0)
        assert snapshot.total_balance == pytest.approx(950.0)
        assert snapshot.mode == "mock"

        latest = ledger.get_latest_snapshot(mode="mock")
        assert latest is not None
        assert latest.total_balance == pytest.approx(950.0)


class TestJsonSerialization:
    def test_to_json(self, evaluator, ledger):
        ledger.record_trade(_make_signal())
        report = evaluator.generate_report()
        json_str = evaluator.to_json(report)
        assert "win_rate" in json_str
        assert "total_pnl" in json_str
