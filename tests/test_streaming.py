"""Tests for WebSocket Streaming Manager."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from polyclaw.config import PolyclawConfig
from polyclaw.streaming import WebSocketManager


@pytest.fixture
def config():
    return PolyclawConfig()


@pytest.fixture
def ws_manager(config):
    return WebSocketManager(config)


def test_on_registers_callback(ws_manager):
    callback = MagicMock()
    ws_manager.on("price_change", callback)
    assert callback in ws_manager.subscribers["price_change"]


def test_on_multiple_callbacks(ws_manager):
    cb1 = MagicMock()
    cb2 = MagicMock()
    ws_manager.on("book", cb1)
    ws_manager.on("book", cb2)
    assert len(ws_manager.subscribers["book"]) == 2


def test_extract_event_type_market(ws_manager):
    data = {"event_type": "price_change"}
    assert ws_manager._extract_event_type(data, "market") == "price_change"


def test_extract_event_type_user(ws_manager):
    data = {"type": "TRADE"}
    assert ws_manager._extract_event_type(data, "user") == "TRADE"


def test_extract_event_type_sports(ws_manager):
    assert ws_manager._extract_event_type({}, "sports") == "sport_result"


def test_extract_event_type_rtds_crypto(ws_manager):
    data = {"topic": "crypto_prices"}
    assert ws_manager._extract_event_type(data, "rtds") == "crypto_prices"


def test_extract_event_type_rtds_comments(ws_manager):
    data = {"topic": "comments"}
    assert ws_manager._extract_event_type(data, "rtds") == "comment_created"


@pytest.mark.asyncio
async def test_dispatch_calls_subscribers(ws_manager):
    results = []

    async def callback(data):
        results.append(data)

    ws_manager.on("test_event", callback)
    await ws_manager._dispatch("test_event", {"key": "value"})

    assert len(results) == 1
    assert results[0] == {"key": "value"}


@pytest.mark.asyncio
async def test_dispatch_wildcard(ws_manager):
    results = []

    async def wildcard_cb(data):
        results.append(data)

    ws_manager.on("*", wildcard_cb)
    await ws_manager._dispatch("any_event", {"data": 1})

    assert len(results) == 1
