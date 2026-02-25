"""Market Fetcher — discovers and filters markets from Polymarket's Gamma API."""

from __future__ import annotations

import time
from typing import Any

import requests

from polyclaw.config import PolyclawConfig
from polyclaw.models import PolymarketEvent, PolymarketMarket
from polyclaw.utils.logging import get_logger

logger = get_logger("fetcher")

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

# Simple in-memory cache: key → (timestamp, data)
_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL = 60  # seconds


def _cached_get(url: str, params: dict | None = None, ttl: int = CACHE_TTL) -> Any:
    """HTTP GET with simple TTL cache."""
    cache_key = f"{url}?{params}"
    now = time.time()
    if cache_key in _cache:
        ts, data = _cache[cache_key]
        if now - ts < ttl:
            return data

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    _cache[cache_key] = (now, data)
    return data


def _parse_market(raw: dict) -> PolymarketMarket:
    """Parse a raw market dict from the Gamma API into a PolymarketMarket."""
    # Outcome prices may come as a JSON string or list
    outcome_prices: dict[str, float] = {}
    raw_prices = raw.get("outcomePrices", "")
    if isinstance(raw_prices, str) and raw_prices:
        import json
        try:
            prices_list = json.loads(raw_prices)
            if len(prices_list) >= 2:
                outcome_prices = {
                    "Yes": float(prices_list[0]),
                    "No": float(prices_list[1]),
                }
        except (json.JSONDecodeError, ValueError, IndexError):
            pass
    elif isinstance(raw_prices, list) and len(raw_prices) >= 2:
        outcome_prices = {
            "Yes": float(raw_prices[0]),
            "No": float(raw_prices[1]),
        }

    # Token IDs
    tokens = raw.get("clobTokenIds", "")
    if isinstance(tokens, str) and tokens:
        import json
        try:
            tokens = json.loads(tokens)
        except json.JSONDecodeError:
            tokens = []
    if not isinstance(tokens, list):
        tokens = []

    token_yes = tokens[0] if len(tokens) > 0 else ""
    token_no = tokens[1] if len(tokens) > 1 else ""

    return PolymarketMarket(
        condition_id=raw.get("conditionId", raw.get("condition_id", "")),
        question=raw.get("question", ""),
        token_id_yes=token_yes,
        token_id_no=token_no,
        tick_size=str(raw.get("minimumTickSize", raw.get("tickSize", "0.01"))),
        neg_risk=bool(raw.get("negRisk", False)),
        enable_order_book=bool(raw.get("enableOrderBook", True)),
        outcome_prices=outcome_prices,
        description=raw.get("description", ""),
        slug=raw.get("slug", ""),
        end_date=raw.get("endDate", raw.get("end_date")),
        closed=bool(raw.get("closed", False)),
    )


def _parse_event(raw: dict) -> PolymarketEvent:
    """Parse a raw event dict from the Gamma API into a PolymarketEvent."""
    raw_markets = raw.get("markets", [])
    markets = [_parse_market(m) for m in raw_markets] if raw_markets else []

    # Tags can be a list of dicts or a list of strings
    raw_tags = raw.get("tags", [])
    if raw_tags and isinstance(raw_tags[0], dict):
        tags = [t.get("label", t.get("slug", "")) for t in raw_tags]
    else:
        tags = [str(t) for t in raw_tags] if raw_tags else []

    return PolymarketEvent(
        id=str(raw.get("id", "")),
        slug=raw.get("slug", ""),
        title=raw.get("title", ""),
        markets=markets,
        tags=tags,
        volume_24hr=float(raw.get("volume24hr", raw.get("volume_24hr", 0)) or 0),
        liquidity=float(raw.get("liquidity", 0) or 0),
        start_date=raw.get("startDate", raw.get("start_date")),
        end_date=raw.get("endDate", raw.get("end_date")),
        closed=bool(raw.get("closed", False)),
    )


class MarketFetcher:
    """Discover and filter markets from the Gamma API."""

    def __init__(self, config: PolyclawConfig):
        self.config = config
        self.base_url = GAMMA_BASE_URL

    def get_active_events(
        self,
        limit: int = 20,
        offset: int = 0,
        order: str = "volume24hr",
        ascending: bool = False,
        tag: str | None = None,
    ) -> list[PolymarketEvent]:
        """Fetch active, open events with optional filtering."""
        params: dict[str, Any] = {
            "active": "true",
            "closed": "false",
            "limit": limit,
            "offset": offset,
            "order": order,
            "ascending": str(ascending).lower(),
        }
        if tag:
            params["tag"] = tag

        data = _cached_get(f"{self.base_url}/events", params=params)

        events = []
        if isinstance(data, list):
            for raw in data:
                ev = _parse_event(raw)
                if self._passes_filters(ev):
                    events.append(ev)
        return events

    def get_event_by_slug(self, slug: str) -> PolymarketEvent | None:
        """Fetch a single event by its slug."""
        data = _cached_get(
            f"{self.base_url}/events", params={"slug": slug}, ttl=30
        )
        if isinstance(data, list) and len(data) > 0:
            return _parse_event(data[0])
        return None

    def get_tags(self) -> list[dict]:
        """Fetch available market tags."""
        return _cached_get(f"{self.base_url}/tags", ttl=300)

    def search_events(self, query: str, limit: int = 10) -> list[PolymarketEvent]:
        """Search events by a text query (title match)."""
        # Gamma API doesn't have a formal search endpoint, so we fetch
        # a larger set and filter locally.
        events = self.get_active_events(limit=100)
        query_lower = query.lower()
        return [
            ev
            for ev in events
            if query_lower in ev.title.lower()
            or any(query_lower in m.question.lower() for m in ev.markets)
        ][:limit]

    def get_market_status(self, condition_id: str) -> dict | None:
        """Check if a market has resolved (for mock P&L settlement)."""
        try:
            data = _cached_get(
                f"{self.base_url}/markets/{condition_id}", ttl=30
            )
            if isinstance(data, dict):
                return data
        except requests.HTTPError:
            logger.warning("Failed to fetch market status for %s", condition_id)
        return None

    def _passes_filters(self, event: PolymarketEvent) -> bool:
        """Check if an event passes the configured filters."""
        filters = self.config.filters

        if event.volume_24hr < filters.min_volume_24hr:
            return False
        if event.liquidity < filters.min_liquidity:
            return False
        if filters.tags_include:
            if not any(t in filters.tags_include for t in event.tags):
                return False
        if filters.tags_exclude:
            if any(t in filters.tags_exclude for t in event.tags):
                return False
        return True


def get_all_token_ids(events: list[PolymarketEvent]) -> list[str]:
    """Extract all token IDs (yes + no) from a list of events."""
    token_ids: list[str] = []
    for ev in events:
        for m in ev.markets:
            if m.token_id_yes:
                token_ids.append(m.token_id_yes)
            if m.token_id_no:
                token_ids.append(m.token_id_no)
    return token_ids
