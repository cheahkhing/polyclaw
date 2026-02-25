"""Tests for the Price Engine."""

from unittest.mock import patch, MagicMock

import pytest

from polyclaw.config import PolyclawConfig
from polyclaw.pricer import PriceEngine


@pytest.fixture
def config():
    return PolyclawConfig()


@pytest.fixture
def pricer_no_sdk(config):
    """Price engine with SDK disabled (REST fallback only)."""
    with patch.object(PriceEngine, "_init_client"):
        engine = PriceEngine(config)
        engine._clob_client = None
        return engine


def test_get_midpoint_rest_fallback(pricer_no_sdk):
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"mid": 0.55}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        mid = pricer_no_sdk.get_midpoint("test_token")
        assert mid == pytest.approx(0.55)


def test_get_price_rest_fallback(pricer_no_sdk):
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"price": 0.42}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        price = pricer_no_sdk.get_price("test_token", "BUY")
        assert price == pytest.approx(0.42)


def test_get_spread(pricer_no_sdk):
    with patch.object(pricer_no_sdk, "get_price") as mock_price:
        mock_price.side_effect = [0.50, 0.55]  # bid, ask

        spread = pricer_no_sdk.get_spread("test_token")
        assert spread["bid"] == pytest.approx(0.50)
        assert spread["ask"] == pytest.approx(0.55)
        assert spread["spread"] == pytest.approx(0.05)


def test_get_orderbook_rest_fallback(pricer_no_sdk):
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "bids": [{"price": "0.50", "size": "100"}],
            "asks": [{"price": "0.55", "size": "50"}],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        book = pricer_no_sdk.get_orderbook("test_token")
        assert len(book["bids"]) == 1
        assert len(book["asks"]) == 1


def test_get_midpoints_batch(pricer_no_sdk):
    with patch.object(pricer_no_sdk, "get_midpoint") as mock_mid:
        mock_mid.side_effect = [0.40, 0.60]

        result = pricer_no_sdk.get_midpoints_batch(["tok1", "tok2"])
        assert result["tok1"] == pytest.approx(0.40)
        assert result["tok2"] == pytest.approx(0.60)
