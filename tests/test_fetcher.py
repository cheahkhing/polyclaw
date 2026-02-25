"""Tests for the Market Fetcher."""

import json
from unittest.mock import patch, MagicMock

import pytest

from polyclaw.config import load_config, PolyclawConfig
from polyclaw.fetcher import MarketFetcher, _parse_event, _parse_market, get_all_token_ids


@pytest.fixture
def config():
    cfg = PolyclawConfig()
    cfg.filters.min_volume_24hr = 0
    cfg.filters.min_liquidity = 0
    return cfg


@pytest.fixture
def fetcher(config):
    return MarketFetcher(config)


@pytest.fixture
def sample_event_raw():
    return {
        "id": "12345",
        "slug": "will-btc-hit-200k",
        "title": "Will BTC hit $200k by Dec 2026?",
        "volume24hr": 50000,
        "liquidity": 100000,
        "startDate": "2025-01-01T00:00:00Z",
        "endDate": "2026-12-31T23:59:59Z",
        "closed": False,
        "tags": [{"label": "crypto"}, {"label": "bitcoin"}],
        "markets": [
            {
                "conditionId": "0xabc123",
                "question": "Will BTC hit $200k by Dec 2026?",
                "clobTokenIds": json.dumps(["token_yes_1", "token_no_1"]),
                "outcomePrices": json.dumps(["0.35", "0.65"]),
                "minimumTickSize": "0.01",
                "negRisk": False,
                "enableOrderBook": True,
                "description": "Resolves Yes if BTC >= $200,000",
                "slug": "btc-200k",
                "endDate": "2026-12-31T23:59:59Z",
                "closed": False,
            }
        ],
    }


def test_parse_market():
    raw = {
        "conditionId": "0xabc",
        "question": "Test market?",
        "clobTokenIds": json.dumps(["yes_tok", "no_tok"]),
        "outcomePrices": json.dumps(["0.60", "0.40"]),
        "minimumTickSize": "0.001",
        "negRisk": True,
        "enableOrderBook": True,
    }
    market = _parse_market(raw)
    assert market.condition_id == "0xabc"
    assert market.question == "Test market?"
    assert market.token_id_yes == "yes_tok"
    assert market.token_id_no == "no_tok"
    assert market.outcome_prices["Yes"] == pytest.approx(0.60)
    assert market.outcome_prices["No"] == pytest.approx(0.40)
    assert market.tick_size == "0.001"
    assert market.neg_risk is True


def test_parse_event(sample_event_raw):
    event = _parse_event(sample_event_raw)
    assert event.id == "12345"
    assert event.slug == "will-btc-hit-200k"
    assert event.title == "Will BTC hit $200k by Dec 2026?"
    assert event.volume_24hr == 50000
    assert event.liquidity == 100000
    assert len(event.markets) == 1
    assert event.tags == ["crypto", "bitcoin"]
    assert event.markets[0].condition_id == "0xabc123"


def test_get_all_token_ids(sample_event_raw):
    event = _parse_event(sample_event_raw)
    tokens = get_all_token_ids([event])
    assert "token_yes_1" in tokens
    assert "token_no_1" in tokens


def test_filter_by_volume(config):
    config.filters.min_volume_24hr = 10000
    fetcher = MarketFetcher(config)

    from polyclaw.models import PolymarketEvent
    low_vol = PolymarketEvent(id="1", slug="low", title="Low Volume", volume_24hr=500, liquidity=50000)
    high_vol = PolymarketEvent(id="2", slug="high", title="High Volume", volume_24hr=20000, liquidity=50000)

    assert fetcher._passes_filters(low_vol) is False
    assert fetcher._passes_filters(high_vol) is True


def test_filter_by_tags(config):
    config.filters.tags_exclude = ["politics"]
    fetcher = MarketFetcher(config)

    from polyclaw.models import PolymarketEvent
    ev = PolymarketEvent(id="1", slug="test", title="Test", tags=["politics"], volume_24hr=5000, liquidity=10000)
    assert fetcher._passes_filters(ev) is False

    ev2 = PolymarketEvent(id="2", slug="test2", title="Test2", tags=["crypto"], volume_24hr=5000, liquidity=10000)
    assert fetcher._passes_filters(ev2) is True


@patch("polyclaw.fetcher._cached_get")
def test_get_active_events(mock_get, fetcher, sample_event_raw):
    mock_get.return_value = [sample_event_raw]
    events = fetcher.get_active_events(limit=10)
    assert len(events) == 1
    assert events[0].title == "Will BTC hit $200k by Dec 2026?"


@patch("polyclaw.fetcher._cached_get")
def test_get_event_by_slug(mock_get, fetcher, sample_event_raw):
    mock_get.return_value = [sample_event_raw]
    event = fetcher.get_event_by_slug("will-btc-hit-200k")
    assert event is not None
    assert event.slug == "will-btc-hit-200k"


@patch("polyclaw.fetcher._cached_get")
def test_get_event_by_slug_not_found(mock_get, fetcher):
    mock_get.return_value = []
    event = fetcher.get_event_by_slug("nonexistent")
    assert event is None
