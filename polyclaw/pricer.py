"""Price Engine — fetches real-time pricing data from the Polymarket CLOB API."""

from __future__ import annotations

from typing import Any

from polyclaw.config import PolyclawConfig
from polyclaw.utils.logging import get_logger

logger = get_logger("pricer")

CLOB_BASE_URL = "https://clob.polymarket.com"


class PriceEngine:
    """Fetches prices, orderbooks, and spread data from the CLOB API.

    Uses ``py-clob-client`` for authenticated calls when available,
    otherwise falls back to direct REST calls for public endpoints.
    """

    def __init__(self, config: PolyclawConfig):
        self.config = config
        self.host = config.polymarket.host or CLOB_BASE_URL
        self._clob_client: Any | None = None
        self._init_client()

    def _init_client(self) -> None:
        """Try to initialise the py-clob-client (read-only, no auth needed)."""
        try:
            from py_clob_client.client import ClobClient

            self._clob_client = ClobClient(
                self.host,
                chain_id=self.config.polymarket.chain_id,
            )
            # Set a shorter timeout on the internal session to avoid
            # blocking the tick loop for too long
            if hasattr(self._clob_client, "session"):
                self._clob_client.session.timeout = 5
            logger.info("CLOB client initialised (read-only)")
        except Exception as exc:
            logger.warning("py-clob-client unavailable, using REST fallback: %s", exc)
            self._clob_client = None

    # ------------------------------------------------------------------
    # Public price reading helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_float(raw: Any, *keys: str) -> float:
        """Coerce an SDK response to float.

        The py-clob-client sometimes returns a plain string/number and
        sometimes a dict like ``{"mid": "0.55"}`` or ``{"price": "0.6"}``.
        Try ``float(raw)`` first; if *raw* is a dict, look for *keys* in
        order, then fall back to the first numeric-looking value.
        """
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            return float(raw)
        if isinstance(raw, dict):
            for k in keys:
                if k in raw:
                    return float(raw[k])
            # Fallback: grab the first value that looks numeric
            for v in raw.values():
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return float(raw)  # last resort — may raise

    def get_midpoint(self, token_id: str) -> float:
        """Return the midpoint price for *token_id* (0.0–1.0)."""
        if self._clob_client:
            try:
                raw = self._clob_client.get_midpoint(token_id)
                return self._to_float(raw, "mid")
            except Exception as exc:
                if "404" in str(exc) or "No orderbook" in str(exc):
                    logger.debug("No orderbook for token %s…", token_id[:20])
                    return 0.0
                logger.warning("get_midpoint via SDK failed: %s", exc)
        return self._rest_midpoint(token_id)

    def get_price(self, token_id: str, side: str = "BUY") -> float:
        """Return the best price for a given *side* (BUY or SELL)."""
        if self._clob_client:
            try:
                raw = self._clob_client.get_price(token_id, side=side)
                return self._to_float(raw, "price")
            except Exception as exc:
                if "404" in str(exc) or "No orderbook" in str(exc):
                    logger.debug("No orderbook for token %s…", token_id[:20])
                    return 0.0
                logger.warning("get_price via SDK failed: %s", exc)
        return self._rest_price(token_id, side)

    def get_spread(self, token_id: str) -> dict[str, float]:
        """Return ``{"bid": ..., "ask": ..., "spread": ...}``."""
        bid = self.get_price(token_id, "BUY")
        ask = self.get_price(token_id, "SELL")
        return {"bid": bid, "ask": ask, "spread": ask - bid}

    def get_orderbook(self, token_id: str) -> dict:
        """Return full orderbook (bids + asks) for *token_id*."""
        if self._clob_client:
            try:
                return self._clob_client.get_order_book(token_id)
            except Exception as exc:
                if "404" in str(exc) or "No orderbook" in str(exc):
                    logger.debug("No orderbook for token %s…", token_id[:20])
                    return {"bids": [], "asks": []}
                logger.warning("get_order_book via SDK failed: %s", exc)
        return self._rest_orderbook(token_id)

    def get_last_trade_price(self, token_id: str) -> float:
        """Return the most recent trade price for *token_id*."""
        if self._clob_client:
            try:
                raw = self._clob_client.get_last_trade_price(token_id)
                return self._to_float(raw, "price", "last_price")
            except Exception as exc:
                if "404" in str(exc) or "No orderbook" in str(exc):
                    logger.debug("No last trade for token %s…", token_id[:20])
                    return 0.0
                logger.warning("get_last_trade_price via SDK failed: %s", exc)
        return 0.0

    def get_midpoints_batch(self, token_ids: list[str]) -> dict[str, float]:
        """Return midpoint prices for multiple tokens.

        Uses the SDK's ``get_order_books`` for a single HTTP call.
        The SDK returns ``OrderBookSummary`` objects (not dicts), so we
        use ``getattr`` to read attributes safely.
        """
        result: dict[str, float] = {}
        if self._clob_client:
            try:
                from py_clob_client.clob_types import BookParams

                books = self._clob_client.get_order_books(
                    [BookParams(token_id=tid) for tid in token_ids]
                )
                if isinstance(books, list):
                    for book in books:
                        # Handle both dict-like and object-like responses
                        if isinstance(book, dict):
                            asset_id = book.get("asset_id", "")
                            mid = book.get("midpoint") or book.get("mid")
                        else:
                            asset_id = getattr(book, "asset_id", "")
                            mid = getattr(book, "midpoint", None) or getattr(book, "mid", None)
                        if asset_id and mid is not None:
                            try:
                                result[asset_id] = float(mid)
                            except (TypeError, ValueError):
                                continue
                    if result:
                        return result
            except Exception as exc:
                logger.warning("Batch midpoint via SDK failed: %s", exc)

        # Fallback: sequential fetches
        for tid in token_ids:
            result[tid] = self.get_midpoint(tid)
        return result

    # ------------------------------------------------------------------
    # REST fallbacks (no SDK)
    # ------------------------------------------------------------------

    def _rest_midpoint(self, token_id: str) -> float:
        import requests

        try:
            resp = requests.get(
                f"{self.host}/midpoint",
                params={"token_id": token_id},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return float(data.get("mid", 0))
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                logger.debug("No orderbook (REST) for %s", token_id[:20])
            else:
                logger.warning("REST midpoint failed for %s: %s", token_id, exc)
            return 0.0
        except Exception as exc:
            logger.warning("REST midpoint failed for %s: %s", token_id, exc)
            return 0.0

    def _rest_price(self, token_id: str, side: str) -> float:
        import requests

        try:
            resp = requests.get(
                f"{self.host}/price",
                params={"token_id": token_id, "side": side},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return float(data.get("price", 0))
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                logger.debug("No orderbook (REST) for %s", token_id[:20])
            else:
                logger.warning("REST price failed for %s: %s", token_id, exc)
            return 0.0
        except Exception as exc:
            logger.warning("REST price failed for %s: %s", token_id, exc)
            return 0.0

    def _rest_orderbook(self, token_id: str) -> dict:
        import requests

        try:
            resp = requests.get(
                f"{self.host}/book",
                params={"token_id": token_id},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                logger.debug("No orderbook (REST) for %s", token_id[:20])
            else:
                logger.warning("REST orderbook failed for %s: %s", token_id, exc)
            return {"bids": [], "asks": []}
        except Exception as exc:
            logger.warning("REST orderbook failed for %s: %s", token_id, exc)
            return {"bids": [], "asks": []}
