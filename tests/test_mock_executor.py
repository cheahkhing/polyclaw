"""Tests for Mock Executor."""

import pytest

from polyclaw.config import PolyclawConfig
from polyclaw.mock_executor import MockExecutor
from polyclaw.models import MarketContext, PolymarketEvent, PolymarketMarket, TradeSignal


@pytest.fixture
def config():
    cfg = PolyclawConfig()
    cfg.mock.starting_balance = 1000.0
    cfg.mock.slippage_bps = 50  # 0.5%
    return cfg


@pytest.fixture
def executor(config):
    return MockExecutor(config=config)


def _make_ctx():
    market = PolymarketMarket(
        condition_id="m1", question="Test?",
        token_id_yes="y1", token_id_no="n1",
        outcome_prices={"Yes": 0.50, "No": 0.50},
    )
    return MarketContext(
        event=PolymarketEvent(id="e1", slug="s", title="T"),
        market=market,
    )


def _make_signal(market_id="m1", token_id="y1", side="BUY", outcome="Yes",
                 price=0.50, size=10.0) -> TradeSignal:
    return TradeSignal(
        market_id=market_id,
        token_id=token_id,
        side=side,
        outcome=outcome,
        price=price,
        size=size,
        confidence=0.8,
        strategy="test",
        reasoning="test trade",
    )


class TestMockExecution:
    def test_initial_balance(self, executor):
        assert executor.balance == 1000.0

    def test_buy_reduces_balance(self, executor):
        signal = _make_signal(price=0.50, size=10.0)
        ctx = _make_ctx()
        result = executor.execute(signal, ctx)
        assert result is not None
        assert result.success is True
        # Cost should be approximately 10 * 0.50 = $5, plus slippage
        assert executor.balance < 1000.0

    def test_sell_increases_balance(self, executor):
        ctx = _make_ctx()
        # First buy to establish a position
        buy = _make_signal(side="BUY", outcome="Yes", price=0.50, size=10.0)
        executor.execute(buy, ctx)
        balance_after_buy = executor.balance

        sell = _make_signal(side="SELL", outcome="Yes", price=0.60, size=5.0)
        result = executor.execute(sell, ctx)
        assert result is not None
        assert executor.balance > balance_after_buy

    def test_insufficient_balance_rejects(self, executor):
        ctx = _make_ctx()
        signal = _make_signal(price=0.50, size=100000.0)
        result = executor.execute(signal, ctx)
        assert result is not None
        assert result.success is False
        assert "Insufficient balance" in result.error


class TestPositionTracking:
    def test_positions_tracked(self, executor):
        ctx = _make_ctx()
        signal = _make_signal(price=0.50, size=10.0)
        executor.execute(signal, ctx)
        positions = executor.get_open_positions()
        assert len(positions) > 0
        assert any(p.token_id == "y1" for p in positions)

    def test_multiple_buys_same_token(self, executor):
        ctx = _make_ctx()
        for _ in range(3):
            signal = _make_signal(price=0.50, size=5.0)
            executor.execute(signal, ctx)
        positions = executor.get_open_positions()
        yes_pos = [p for p in positions if p.outcome == "Yes"]
        assert len(yes_pos) == 1  # should be aggregated
        assert yes_pos[0].size == pytest.approx(15.0, abs=1.0)


class TestMarketResolution:
    def test_resolution_profitable(self, executor):
        ctx = _make_ctx()
        signal = _make_signal(price=0.40, size=10.0)
        executor.execute(signal, ctx)
        # Resolve yes as winner → each share pays $1
        pnl = executor.resolve_market("m1", winning_outcome="Yes")
        assert pnl > 0

    def test_resolution_unprofitable(self, executor):
        ctx = _make_ctx()
        signal = _make_signal(price=0.60, size=10.0)
        executor.execute(signal, ctx)
        # Resolve no as winner → yes shares worthless
        pnl = executor.resolve_market("m1", winning_outcome="No")
        assert pnl < 0
