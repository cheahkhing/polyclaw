"""FastAPI dashboard application — REST API + WebSocket + template rendering."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from polyclaw.config import PolyclawConfig, load_config
from polyclaw.dashboard.ws_handler import WebSocketConnectionManager
from polyclaw.event_bus import EventBus
from polyclaw.simulator import SimScheduler
from polyclaw.strategy import StrategyRegistry
from polyclaw.strategies.sports_volatility import SportsVolatilityStrategy
from polyclaw.utils.logging import get_logger

logger = get_logger("dashboard.app")

# Paths
DASHBOARD_DIR = Path(__file__).parent
TEMPLATES_DIR = DASHBOARD_DIR / "templates"
STATIC_DIR = DASHBOARD_DIR / "static"


def create_app(config: PolyclawConfig | None = None) -> FastAPI:
    """Create and configure the FastAPI dashboard application."""
    config = config or load_config()

    app = FastAPI(
        title="Polyclaw Simulation Dashboard",
        description="Real-time monitoring for Polymarket simulation trading",
        version="0.1.0",
    )

    # Mount static files
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Templates
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # Shared state
    event_bus = EventBus()
    ws_manager = WebSocketConnectionManager()
    registry = StrategyRegistry()

    # Register default strategies
    sports_strat = SportsVolatilityStrategy()
    sports_strat.configure(config)
    registry.register(sports_strat)

    # Create simulator
    scheduler = SimScheduler(config, event_bus=event_bus, strategy_registry=registry)

    # Wire EventBus → WebSocket manager
    event_bus.subscribe("*", ws_manager.on_sim_event)

    # Store on app for access in routes
    app.state.config = config
    app.state.event_bus = event_bus
    app.state.ws_manager = ws_manager
    app.state.scheduler = scheduler
    app.state.templates = templates

    # Scan results cache (in-memory for current session)
    app.state.last_scan: list[dict] = []

    # ── Lifecycle ─────────────────────────────────────────────────

    @app.on_event("startup")
    async def on_startup():
        """Capture the main event loop so the WS manager can broadcast
        from the simulator's background thread."""
        import asyncio
        ws_manager.set_loop(asyncio.get_running_loop())

    # ── Pages ──────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def monitor_page(request: Request):
        """Live simulation monitor page."""
        state = scheduler.get_state()
        strategies = [
            {"name": s.name, "description": s.description}
            for s in registry.get_all()
        ]
        return templates.TemplateResponse("monitor.html", {
            "request": request,
            "state": state,
            "config": config,
            "strategies": strategies,
            "ws_url": f"ws://{config.dashboard.host}:{config.dashboard.port}/ws/sim",
        })

    @app.get("/runs", response_class=HTMLResponse)
    async def runs_page(request: Request):
        """Run history page."""
        runs = scheduler.ledger.get_sim_runs(limit=50)
        return templates.TemplateResponse("runs.html", {
            "request": request,
            "runs": runs,
        })

    # ── REST API ──────────────────────────────────────────────────

    @app.get("/api/sim/state")
    async def get_sim_state():
        """Get current simulation state."""
        state = scheduler.get_state()
        state["watchlist"] = list(scheduler._watchlist) if scheduler._watchlist else []
        return state

    @app.post("/api/scan")
    async def scan_markets(strategy: str = "sports_volatility"):
        """One-shot scan: fetch markets, filter and score candidates.

        Returns a list of candidate dicts, does NOT start the simulation.
        Runs in a thread pool so sync HTTP calls don't block the event loop.
        """
        import asyncio
        loop = asyncio.get_running_loop()
        try:
            candidates = await loop.run_in_executor(
                None, lambda: scheduler.scan(strategy_name=strategy)
            )
            app.state.last_scan = candidates
            return {"candidates": candidates, "count": len(candidates)}
        except Exception as exc:
            logger.error("Scan failed: %s", exc)
            return JSONResponse(status_code=500, content={"error": str(exc)})

    @app.post("/api/sim/start")
    async def start_sim(
        strategy: str = "sports_volatility",
        tick_interval: int | None = None,
        duration_minutes: int | None = None,
    ):
        """Start a monitoring simulation.

        Expects JSON body with optional token_ids list to restrict
        monitoring to only those markets (watchlist).
        """
        try:
            run = scheduler.start(
                strategy_name=strategy,
                tick_interval=tick_interval,
                duration_minutes=duration_minutes,
            )
            return {"status": "started", "run_id": run.run_id}
        except RuntimeError as exc:
            return JSONResponse(status_code=409, content={"error": str(exc)})

    @app.post("/api/sim/watchlist")
    async def set_watchlist(request: Request):
        """Set the watchlist of token IDs to monitor.

        Body: { "token_ids": ["token1", "token2", ...] }
        """
        body = await request.json()
        token_ids = body.get("token_ids", [])
        scheduler.set_watchlist(token_ids)
        return {"watchlist": token_ids, "count": len(token_ids)}

    @app.post("/api/sim/pause")
    async def pause_sim():
        """Pause the running simulation."""
        scheduler.pause()
        return {"status": "paused"}

    @app.post("/api/sim/resume")
    async def resume_sim():
        """Resume the simulation."""
        scheduler.resume()
        return {"status": "resumed"}

    @app.post("/api/sim/stop")
    async def stop_sim():
        """Stop the simulation."""
        scheduler.stop()
        return {"status": "stopped"}

    @app.get("/api/sim/runs")
    async def list_runs():
        """List all simulation runs."""
        runs = scheduler.ledger.get_sim_runs(limit=50)
        return [
            {
                "run_id": r.run_id,
                "strategy": r.strategy,
                "started_at": r.started_at,
                "ended_at": r.ended_at,
                "status": r.status,
                "notes": r.notes,
            }
            for r in runs
        ]

    @app.get("/api/sim/trades")
    async def get_trades(limit: int = 100):
        """Get recent trades."""
        trades = scheduler.ledger.get_trades(mode="mock", limit=limit)
        return trades

    @app.get("/api/sim/positions")
    async def get_positions():
        """Get current open positions."""
        positions = scheduler.executor.get_open_positions()
        return [
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
        ]

    @app.get("/api/sim/snapshots")
    async def get_snapshots(limit: int = 200):
        """Get portfolio snapshots for P&L curve."""
        snapshots = scheduler.ledger.get_snapshots(mode="mock", limit=limit)
        return [
            {
                "timestamp": s.timestamp,
                "total_balance": s.total_balance,
                "unrealized_pnl": s.unrealized_pnl,
                "realized_pnl": s.realized_pnl,
                "open_positions": s.open_positions,
                "total_trades": s.total_trades,
            }
            for s in snapshots
        ]

    @app.get("/api/sim/report")
    async def get_report():
        """Get evaluation report."""
        report = scheduler.evaluator.generate_report(mode="mock")
        return {
            "win_rate": report.win_rate,
            "total_pnl": report.total_pnl,
            "avg_return": report.avg_return,
            "max_drawdown": report.max_drawdown,
            "total_trades": report.total_trades,
            "open_positions": report.open_positions,
            "unrealized_pnl": report.unrealized_pnl,
            "portfolio_balance": report.portfolio_balance,
            "strategy_breakdown": report.strategy_breakdown,
        }

    @app.get("/api/config")
    async def get_config():
        """Get current configuration."""
        d = asdict(config)
        # Remove sensitive fields
        if "polymarket" in d:
            d["polymarket"].pop("private_key_env", None)
            d["polymarket"].pop("funder_env", None)
        return d

    @app.get("/api/strategies")
    async def get_strategies():
        """List registered strategies."""
        return [
            {"name": s.name, "description": s.description}
            for s in registry.get_all()
        ]

    # ── WebSocket ─────────────────────────────────────────────────

    @app.websocket("/ws/sim")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for real-time simulation updates."""
        await ws_manager.connect(websocket)
        try:
            while True:
                # Keep connection alive — read pings/pongs
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
        except Exception:
            ws_manager.disconnect(websocket)

    return app


def run_dashboard(config: PolyclawConfig | None = None) -> None:
    """Launch the dashboard server."""
    import uvicorn

    config = config or load_config()
    app = create_app(config)

    logger.info(
        "Starting dashboard at http://%s:%d",
        config.dashboard.host,
        config.dashboard.port,
    )

    uvicorn.run(
        app,
        host=config.dashboard.host,
        port=config.dashboard.port,
        log_level="info",
    )
