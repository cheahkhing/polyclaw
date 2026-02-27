"""SimScheduler — orchestrates simulation tick loop."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from polyclaw.config import PolyclawConfig
from polyclaw.evaluator import Evaluator
from polyclaw.event_bus import EventBus
from polyclaw.fetcher import MarketFetcher
from polyclaw.ledger import TradeLedger
from polyclaw.mock_executor import MockExecutor
from polyclaw.models import (
    MarketContext,
    MockTradeResult,
    PolymarketEvent,
    PolymarketMarket,
    SimEvent,
    SimRun,
)
from polyclaw.pricer import PriceEngine
from polyclaw.recorder import PriceRecorder
from polyclaw.risk import RiskGate
from polyclaw.strategy import BaseStrategy, StrategyRegistry
from polyclaw.utils.logging import get_logger

logger = get_logger("simulator")


class SimScheduler:
    """Orchestrates the simulation tick loop.

    Coordinates market fetching, strategy evaluation, risk checking,
    and mock execution. Publishes events through the EventBus to support
    the web dashboard.
    """

    def __init__(
        self,
        config: PolyclawConfig,
        event_bus: EventBus | None = None,
        strategy_registry: StrategyRegistry | None = None,
    ):
        self.config = config
        self.event_bus = event_bus or EventBus()
        self.registry = strategy_registry or StrategyRegistry()

        # Core components
        self.fetcher = MarketFetcher(config)
        self.pricer = PriceEngine(config)
        self.executor = MockExecutor(config)
        self.ledger = TradeLedger(config)
        self.evaluator = Evaluator(config, self.ledger)

        # Risk gate
        self.risk_gate = RiskGate(
            config=config.risk,
            get_open_position_count=lambda: len(self.executor.get_open_positions()),
            get_today_trade_count=lambda: self.ledger.get_today_trade_count("mock"),
            get_balance=lambda: self.executor.balance,
        )

        # Price recorder
        self.recorder: PriceRecorder | None = None
        if config.simulation.record_prices:
            self.recorder = PriceRecorder(config.simulation.price_db_path)

        # State
        self.run: SimRun | None = None
        self._running = False
        self._paused = False
        self._thread: threading.Thread | None = None
        self._tick_count = 0
        self._last_events: list[PolymarketEvent] = []
        self._watchlist: set[str] | None = None  # token_ids to monitor (None = all)
        self._last_scan_id: str | None = None

        # Sim config
        self._tick_interval = config.simulation.default_tick_interval_seconds
        self._duration_minutes = config.simulation.default_duration_minutes
        self._snapshot_every = config.simulation.snapshot_every_n_ticks

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def tick_count(self) -> int:
        return self._tick_count

    # ── Scan & Watchlist ──────────────────────────────────────────

    def scan(self, strategy_name: str | None = None) -> list[dict]:
        """One-shot scan: fetch markets, build contexts, ask strategy to
        score candidates.  Does NOT start the tick loop.

        Uses Gamma API prices (already fetched) for speed — avoids
        per-market CLOB API calls which would take minutes.

        Returns a list of candidate dicts sorted by score.
        """
        logger.info("Scan starting — fetching events from Gamma API …")
        events = self.fetcher.get_active_events(limit=50)
        self._last_events = events
        logger.info("Fetched %d events, building contexts …", len(events))

        # Build contexts using cached Gamma prices (fast path)
        contexts: list[MarketContext] = []
        for event in events:
            for market in event.markets:
                if market.closed or not market.token_id_yes:
                    continue
                ctx = self._build_scan_context(event, market)
                if ctx is not None:
                    contexts.append(ctx)

        candidates: list[dict] = []
        if strategy_name:
            strategy = self.registry.get(strategy_name)
            candidates = strategy.scan_candidates(contexts)
        else:
            for strategy in self.registry.get_all():
                candidates.extend(strategy.scan_candidates(contexts))
            candidates.sort(key=lambda c: c.get("score", 0), reverse=True)

        # Record scan session
        scan_id = str(uuid.uuid4())[:8]
        self._last_scan_id = scan_id
        if self.recorder:
            self.recorder.record_scan_session(scan_id, strategy_name or "all", candidates)

        logger.info(
            "Scan complete: %d events → %d contexts → %d candidates",
            len(events), len(contexts), len(candidates),
        )
        return candidates

    def set_watchlist(self, token_ids: list[str]) -> None:
        """Restrict simulation to only these token IDs."""
        self._watchlist = set(token_ids) if token_ids else None
        logger.info("Watchlist set: %d tokens", len(self._watchlist) if self._watchlist else 0)

    def start(
        self,
        strategy_name: str | None = None,
        tick_interval: int | None = None,
        duration_minutes: int | None = None,
    ) -> SimRun:
        """Start a new simulation run in a background thread."""
        if self._running:
            raise RuntimeError("Simulation already running")

        self._tick_interval = tick_interval or self._tick_interval
        self._duration_minutes = duration_minutes or self._duration_minutes

        # Create run record
        import json
        self.run = SimRun(
            run_id=str(uuid.uuid4())[:8],
            strategy=strategy_name or "all",
            started_at=datetime.now(timezone.utc).isoformat(),
            config_snapshot=json.dumps(asdict(self.config), default=str),
            status="running",
        )

        # Save run to ledger
        self.ledger.save_sim_run(self.run)

        self._running = True
        self._paused = False
        self._tick_count = 0

        self._publish_event("sim_status", {
            "status": "running",
            "run_id": self.run.run_id,
            "strategy": self.run.strategy,
            "tick_interval": self._tick_interval,
            "duration_minutes": self._duration_minutes,
        })

        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        logger.info(
            "Simulation started: run_id=%s, strategy=%s, tick=%ds, duration=%dm",
            self.run.run_id, self.run.strategy,
            self._tick_interval, self._duration_minutes,
        )
        return self.run

    def pause(self) -> None:
        """Pause the simulation."""
        self._paused = True
        self._publish_event("sim_status", {"status": "paused", "run_id": self.run.run_id if self.run else ""})

    def resume(self) -> None:
        """Resume a paused simulation."""
        self._paused = False
        self._publish_event("sim_status", {"status": "running", "run_id": self.run.run_id if self.run else ""})

    def stop(self) -> None:
        """Stop the simulation gracefully.

        Sets the running flag to False; the background thread will
        finish its current tick and then exit.  Does NOT block waiting
        for the thread — the finalize callback will fire sim_status.
        """
        self._running = False
        logger.info("Simulation stop requested")
        self._publish_event("sim_status", {
            "status": "stopped",
            "run_id": self.run.run_id if self.run else "",
        })

    def _run_loop(self) -> None:
        """Main tick loop — runs in background thread."""
        start_time = time.time()
        max_seconds = self._duration_minutes * 60

        try:
            while self._running:
                # Check duration
                elapsed = time.time() - start_time
                if elapsed >= max_seconds:
                    logger.info("Simulation duration reached (%dm)", self._duration_minutes)
                    break

                if self._paused:
                    time.sleep(1)
                    continue

                self._tick_count += 1
                try:
                    self._execute_tick()
                except Exception as exc:
                    logger.error("Tick %d error: %s", self._tick_count, exc)
                    self._publish_event("error", {
                        "tick": self._tick_count,
                        "error": str(exc),
                    })

                # Wait for next tick
                time.sleep(self._tick_interval)

        except Exception as exc:
            logger.error("Simulation loop crashed: %s", exc)
            self._publish_event("error", {"error": str(exc), "fatal": True})
        finally:
            self._finalize()

    def _execute_tick(self) -> None:
        """Execute one tick of the simulation."""
        logger.debug("Tick %d start", self._tick_count)

        # 1. Fetch active events
        try:
            # Use tag filter if strategy specifies one
            tag = None
            events = self.fetcher.get_active_events(limit=50, tag=tag)
            self._last_events = events
        except Exception as exc:
            logger.warning("Failed to fetch events: %s", exc)
            events = self._last_events  # Use cached

        self._publish_event("tick", {
            "tick": self._tick_count,
            "events_count": len(events),
        })

        self._publish_event("events_scanned", {
            "events": [
                {
                    "id": ev.id,
                    "title": ev.title,
                    "slug": ev.slug,
                    "tags": ev.tags,
                    "volume_24hr": ev.volume_24hr,
                    "liquidity": ev.liquidity,
                    "end_date": ev.end_date,
                    "markets_count": len(ev.markets),
                }
                for ev in events[:30]
            ],
        })

        # Pre-fetch midpoints in batch for watched tokens (much faster)
        prefetched: dict[str, float] = {}
        if self._watchlist:
            try:
                prefetched = self.pricer.get_midpoints_batch(list(self._watchlist))
                logger.debug("Pre-fetched %d midpoints for watchlist", len(prefetched))
            except Exception as exc:
                logger.warning("Batch midpoint fetch failed: %s", exc)

        # 2. Build context and evaluate each market
        for event in events:
            for market in event.markets:
                if market.closed or not market.token_id_yes:
                    continue

                # Watchlist filter: skip markets not in watchlist
                if self._watchlist and market.token_id_yes not in self._watchlist:
                    continue

                context = self._build_context(
                    event, market, prefetched_midpoint=prefetched.get(market.token_id_yes)
                )
                if context is None:
                    continue

                # Record price if configured
                if self.recorder and context.midpoint > 0:
                    try:
                        spread_data = {}
                        if context.spread > 0:
                            spread_data = {"spread": context.spread}
                        self.recorder.record_tick(
                            token_id=market.token_id_yes,
                            midpoint=context.midpoint,
                            spread=context.spread,
                        )
                        self.recorder.record_event_metadata(event)
                    except Exception:
                        pass

                # Publish price update
                self._publish_event("price_update", {
                    "token_id": market.token_id_yes,
                    "market_id": market.condition_id,
                    "title": event.title,
                    "midpoint": context.midpoint,
                    "spread": context.spread,
                })

                # 3. Run each strategy
                for strategy in self.registry.get_all():
                    signal = strategy.evaluate(context)
                    if signal:
                        self._publish_event("signal_emitted", {
                            "strategy": signal.strategy,
                            "market_title": signal.market_title,
                            "side": signal.side,
                            "outcome": signal.outcome,
                            "price": signal.price,
                            "size": signal.size,
                            "confidence": signal.confidence,
                            "reasoning": signal.reasoning,
                        })

                        # Risk check
                        verdict = self.risk_gate.check(signal)
                        self._publish_event("risk_verdict", {
                            "approved": verdict.approved,
                            "reason": verdict.reason,
                            "side": signal.side,
                            "price": signal.price,
                        })

                        if verdict.approved:
                            result = self.executor.execute(signal, context)
                            self.ledger.record_mock_result(result)

                            self._publish_event("trade_executed", {
                                "trade_id": result.trade_id,
                                "side": signal.side,
                                "outcome": signal.outcome,
                                "price": signal.price,
                                "fill_price": result.fill_price,
                                "size": signal.size,
                                "slippage": result.slippage,
                                "balance_after": result.balance_after,
                                "success": result.success,
                                "market_title": signal.market_title,
                                "strategy": signal.strategy,
                                "error": result.error,
                            })

        # 4. Check open positions for exits
        self._check_exits(events)

        # 5. Update position prices and publish
        self._update_positions()

        # 6. Take snapshot periodically
        if self._tick_count % self._snapshot_every == 0:
            snapshot = self.evaluator.take_snapshot(
                balance=self.executor.balance, mode="mock"
            )
            self._publish_event("snapshot", {
                "balance": snapshot.total_balance,
                "unrealized_pnl": snapshot.unrealized_pnl,
                "realized_pnl": snapshot.realized_pnl,
                "open_positions": snapshot.open_positions,
                "total_trades": snapshot.total_trades,
            })

    def _build_context(
        self,
        event: PolymarketEvent,
        market: PolymarketMarket,
        prefetched_midpoint: float | None = None,
    ) -> MarketContext | None:
        """Build MarketContext with live pricing data.

        If *prefetched_midpoint* is provided (from a batch call), it is
        used directly — saving one HTTP round-trip per market.
        """
        if prefetched_midpoint is not None and prefetched_midpoint > 0:
            midpoint = prefetched_midpoint
        else:
            try:
                midpoint = self.pricer.get_midpoint(market.token_id_yes)
            except Exception:
                midpoint = market.outcome_prices.get("Yes", 0.0)

        if midpoint <= 0:
            return None

        try:
            spread_data = self.pricer.get_spread(market.token_id_yes)
            spread = spread_data.get("spread", 0.0)
        except Exception:
            spread = 0.0

        # Calculate time to resolution
        time_to_resolution = None
        end_date_str = market.end_date or event.end_date
        if end_date_str:
            try:
                end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                time_to_resolution = end_dt - now
            except (ValueError, TypeError):
                pass

        return MarketContext(
            event=event,
            market=market,
            midpoint=midpoint,
            spread=spread,
            volume_24hr=event.volume_24hr,
            time_to_resolution=time_to_resolution,
        )

    def _build_scan_context(
        self, event: PolymarketEvent, market: PolymarketMarket
    ) -> MarketContext | None:
        """Build a lightweight MarketContext using Gamma API prices only.

        This avoids per-market CLOB HTTP calls and is used during scan
        to quickly score candidates.  The midpoint comes from
        ``market.outcome_prices["Yes"]`` which the Gamma API already
        provides.  Spread is estimated as a small fixed value (CLOB
        spread is fetched later during live monitoring).
        """
        midpoint = market.outcome_prices.get("Yes", 0.0)
        if midpoint <= 0:
            return None

        # Estimate spread — Gamma doesn't provide it, use a placeholder
        # The real spread will be fetched during monitoring via CLOB API
        spread = 0.02  # conservative default

        # Calculate time to resolution
        time_to_resolution = None
        end_date_str = market.end_date or event.end_date
        if end_date_str:
            try:
                end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                time_to_resolution = end_dt - now
            except (ValueError, TypeError):
                pass

        return MarketContext(
            event=event,
            market=market,
            midpoint=midpoint,
            spread=spread,
            volume_24hr=event.volume_24hr,
            time_to_resolution=time_to_resolution,
        )

    def _check_exits(self, events: list[PolymarketEvent]) -> None:
        """Check open positions for exit signals."""
        positions = self.executor.get_open_positions()
        if not positions:
            return

        # Pre-fetch midpoints for position tokens
        pos_token_ids = [p.token_id for p in positions]
        try:
            prefetched = self.pricer.get_midpoints_batch(pos_token_ids)
        except Exception:
            prefetched = {}

        # Build a map of market_id -> (event, market) for quick lookup
        market_map: dict[str, tuple] = {}
        for ev in events:
            for m in ev.markets:
                market_map[m.condition_id] = (ev, m)

        for pos in positions:
            if pos.market_id not in market_map:
                continue

            event, market = market_map[pos.market_id]
            context = self._build_context(
                event, market, prefetched_midpoint=prefetched.get(pos.token_id)
            )
            if context is None:
                continue

            for strategy in self.registry.get_all():
                close_signal = strategy.should_close(pos, context)
                if close_signal:
                    verdict = self.risk_gate.check(close_signal)
                    if verdict.approved:
                        result = self.executor.execute(close_signal, context)
                        self.ledger.record_mock_result(result)

                        self._publish_event("trade_executed", {
                            "trade_id": result.trade_id,
                            "side": "SELL",
                            "outcome": pos.outcome,
                            "price": close_signal.price,
                            "fill_price": result.fill_price,
                            "size": close_signal.size,
                            "balance_after": result.balance_after,
                            "success": result.success,
                            "market_title": close_signal.market_title,
                            "strategy": close_signal.strategy,
                            "reasoning": close_signal.reasoning,
                        })
                    break  # Only one exit per position per tick

    def _update_positions(self) -> None:
        """Update current prices for all open positions."""
        positions = self.executor.get_open_positions()
        if not positions:
            self._publish_event("position_updated", {"positions": []})
            return

        # Use batch API for efficiency
        token_ids = [pos.token_id for pos in positions]
        try:
            prices = self.pricer.get_midpoints_batch(token_ids)
        except Exception:
            prices = {}

        self.executor.update_position_prices(prices)

        # Publish all positions as a single event
        updated = self.executor.get_open_positions()
        self._publish_event("position_updated", {
            "positions": [
                {
                    "market_id": p.market_id,
                    "condition_id": p.market_id,
                    "token_id": p.token_id,
                    "outcome": p.outcome,
                    "side": "BUY",
                    "entry_price": p.entry_price,
                    "current_price": p.current_price,
                    "avg_price": p.entry_price,
                    "size": p.size,
                    "unrealized_pnl": p.unrealized_pnl,
                    "strategy": p.strategy,
                    "market": getattr(p, 'market_title', p.market_id),
                }
                for p in updated
            ],
        })

    def _finalize(self) -> None:
        """Finalize the simulation run."""
        self._running = False

        if self.run:
            self.run.ended_at = datetime.now(timezone.utc).isoformat()
            self.run.status = "completed"
            self.ledger.save_sim_run(self.run)

        # Final snapshot
        try:
            snapshot = self.evaluator.take_snapshot(
                balance=self.executor.balance, mode="mock"
            )
        except Exception:
            pass

        # Generate report
        try:
            report = self.evaluator.generate_report(mode="mock")
            self._publish_event("sim_status", {
                "status": "completed",
                "run_id": self.run.run_id if self.run else "",
                "total_trades": report.total_trades,
                "total_pnl": report.total_pnl,
                "win_rate": report.win_rate,
                "portfolio_balance": report.portfolio_balance,
            })
        except Exception as exc:
            logger.error("Failed to generate final report: %s", exc)
            self._publish_event("sim_status", {
                "status": "completed",
                "run_id": self.run.run_id if self.run else "",
                "error": str(exc),
            })

        logger.info("Simulation finalized: %s", self.run.run_id if self.run else "?")

    def _publish_event(self, event_type: str, data: dict) -> None:
        """Publish a SimEvent through the EventBus."""
        event = SimEvent(
            type=event_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            data=data,
        )
        try:
            self.event_bus.publish(event)
        except Exception as exc:
            logger.debug("EventBus publish error: %s", exc)

    def get_state(self) -> dict:
        """Get current simulation state for dashboard."""
        positions = self.executor.get_open_positions()
        status = "idle"
        if self.run:
            if self._running and not self._paused:
                status = "running"
            elif self._paused:
                status = "paused"
            else:
                status = self.run.status or "idle"
        return {
            "status": status,
            "run": {
                "run_id": self.run.run_id if self.run else None,
                "strategy": self.run.strategy if self.run else None,
                "started_at": self.run.started_at if self.run else None,
                "status": self.run.status if self.run else "idle",
            },
            "is_running": self._running,
            "is_paused": self._paused,
            "tick_count": self._tick_count,
            "balance": self.executor.balance,
            "positions": [
                {
                    "market_id": p.market_id,
                    "token_id": p.token_id,
                    "outcome": p.outcome,
                    "entry_price": p.entry_price,
                    "current_price": p.current_price,
                    "size": p.size,
                    "unrealized_pnl": p.unrealized_pnl,
                    "strategy": p.strategy,
                }
                for p in positions
            ],
            "trade_count": len(self.executor.trade_log),
            "events_count": len(self._last_events),
        }
