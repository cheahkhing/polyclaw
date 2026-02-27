# Polyclaw — Architecture & Implementation Guide

> Local simulation trading framework for [Polymarket](https://polymarket.com) prediction markets, focused on sports events.

---

## Overview

Polyclaw is a **paper-trading simulation framework** that connects to Polymarket's public APIs, discovers sports betting markets, evaluates them with pluggable strategies, and executes simulated (mock) trades. It provides a real-time **web dashboard** for scanning, selecting, and monitoring markets.

### Key Design Decisions

| Decision | Rationale |
|---|---|
| Mock-only execution | Safe experimentation — no real funds at risk |
| Two-phase workflow (Scan → Monitor) | User selects which markets to watch before simulation starts |
| EventBus pub/sub | Loose coupling between simulator background thread and async dashboard |
| Gamma API for scan, CLOB API for monitoring | Gamma provides prices for free in bulk; CLOB is used only for live-traded markets |
| SQLite for persistence | Zero-config, portable, no external DB needed |

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Web Dashboard (FastAPI)                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐ │
│  │ REST API │  │ WebSocket│  │ Jinja2   │  │ Static Files │ │
│  │ /api/*   │  │ /ws/sim  │  │ Templates│  │ JS / CSS     │ │
│  └────┬─────┘  └────┬─────┘  └──────────┘  └──────────────┘ │
│       │              │                                        │
│       ▼              ▲                                        │
│  ┌─────────┐    ┌─────────┐                                  │
│  │SimSched │───▶│EventBus │  (cross-thread via               │
│  │  uler   │    │         │   call_soon_threadsafe)           │
│  └────┬────┘    └─────────┘                                  │
│       │                                                       │
│  ┌────▼────────────────────────────────────┐                 │
│  │           Tick Loop (Thread)            │                 │
│  │  Fetcher → Pricer → Strategy → Risk →  │                 │
│  │  MockExecutor → Ledger → Recorder      │                 │
│  └─────────────────────────────────────────┘                 │
└──────────────────────────────────────────────────────────────┘
```

---

## Module Reference

### Core Pipeline

| Module | File | Purpose |
|---|---|---|
| **MarketFetcher** | `polyclaw/fetcher.py` | Discover active events from Polymarket's Gamma API. Supports tag filtering, pagination, and in-memory caching (60s TTL). |
| **PriceEngine** | `polyclaw/pricer.py` | Fetch real-time prices from the Polymarket CLOB API via `py-clob-client`. Provides `get_midpoint()`, `get_spread()`, `get_price()`, `get_midpoints_batch()` with REST fallbacks. |
| **BaseStrategy** | `polyclaw/strategy.py` | Abstract base class for trading strategies. Defines `configure()`, `evaluate()`, `should_close()`, and `scan_candidates()`. Includes `StrategyRegistry` for plugin discovery. |
| **RiskGate** | `polyclaw/risk.py` | Pre-trade validation: confidence threshold, max position size, max open positions, daily trade limit, balance check. Returns `RiskVerdict`. |
| **MockExecutor** | `polyclaw/mock_executor.py` | Paper-trade execution. Simulates fills at midpoint with configurable slippage (default 10 bps). Tracks virtual balance and open positions. |
| **TradeLedger** | `polyclaw/ledger.py` | SQLite persistence for trades, positions, snapshots, and simulation runs. |
| **Evaluator** | `polyclaw/evaluator.py` | Portfolio snapshots, P&L calculation, win-rate, max drawdown, strategy breakdown reports. |

### Simulation Engine

| Module | File | Purpose |
|---|---|---|
| **SimScheduler** | `polyclaw/simulator.py` | Orchestrates the tick loop. Manages scan, watchlist, start/stop/pause lifecycle. Runs the tick loop in a background `threading.Thread`. |
| **EventBus** | `polyclaw/event_bus.py` | In-process pub/sub. Sync and async subscribers. Wildcard `*` support. Bridges the simulator thread to the async dashboard via `call_soon_threadsafe`. |
| **PriceRecorder** | `polyclaw/recorder.py` | Records price ticks and event metadata to SQLite (`price_history.db`) for future replay/backtesting. Also stores scan sessions. |
| **SimExporter** | `polyclaw/exporter.py` | Export simulation trades to CSV, JSON, or Markdown. |

### Dashboard

| Module | File | Purpose |
|---|---|---|
| **FastAPI App** | `polyclaw/dashboard/app.py` | REST API + WebSocket + Jinja2 template rendering. Creates `SimScheduler`, wires `EventBus → WebSocketManager`. |
| **WS Manager** | `polyclaw/dashboard/ws_handler.py` | WebSocket connection manager. Stores the asyncio event loop reference; uses `call_soon_threadsafe` to broadcast from the sim thread. |
| **Templates** | `polyclaw/dashboard/templates/` | `base.html` (layout, Pico CSS, Lightweight Charts CDN), `monitor.html` (two-phase scan/monitor UI), `runs.html` (run history). |
| **Static** | `polyclaw/dashboard/static/` | `app.js` (scan/select/monitor workflow, WebSocket event routing, chart), `style.css` (badges, stat values, trade feed). |

### Configuration

| Module | File | Purpose |
|---|---|---|
| **Config** | `polyclaw/config.py` | Dataclass-based config: `MockConfig`, `PolymarketConfig`, `RiskConfig`, `SimConfig`, `DashboardConfig`, etc. Loaded from `polyclaw.config.json` with env var overrides. |
| **CLI** | `polyclaw/cli.py` | Click CLI: `markets`, `prices`, `evaluate`, `sim run`, `sim list`, `sim export`, `dashboard`. |

---

## Data Models

All models are defined in `polyclaw/models.py` as `@dataclass` classes:

| Model | Purpose |
|---|---|
| `PolymarketEvent` | Event container from Gamma API (id, title, slug, tags, markets, volume, dates) |
| `PolymarketMarket` | Single binary market (condition_id, question, token_ids, outcome_prices, end_date) |
| `MarketContext` | Strategy input: event + market + live pricing (midpoint, spread, volume, time_to_resolution) |
| `TradeSignal` | Strategy output: side, outcome, price, size, confidence, reasoning |
| `Position` | Open/closed position with entry/current prices and P&L |
| `MockTradeResult` | Result of simulated execution (fill_price, slippage, balance_after) |
| `RiskVerdict` | Risk gate decision (approved/rejected + reason) |
| `SimRun` | Simulation run metadata (run_id, strategy, timestamps, status) |
| `SimEvent` | Pub/sub event payload (type, timestamp, data dict) |
| `PortfolioSnapshot` | Point-in-time balance, P&L, position counts |
| `EvaluationReport` | Aggregated performance metrics (win_rate, total_pnl, drawdown) |

---

## Dashboard Workflow

### Phase 1 — Scan

1. User selects a strategy from the dropdown
2. Clicks **🔍 Scan Markets**
3. `POST /api/scan?strategy=sports_volatility` triggers:
   - Fetch ~50 active events from Gamma API
   - Build lightweight `MarketContext` for each market (using Gamma prices, no CLOB calls)
   - Strategy's `scan_candidates()` filters and scores them
4. Candidates table renders with checkboxes (all selected by default)
5. Each row shows: Score, Event/Market name (with 🔗 Polymarket link), Price, Spread, Volume, Resolution time, Reasoning

### Phase 2 — Monitor

1. User (de)selects markets, clicks **▶ Start Monitoring**
2. `POST /api/sim/watchlist` sets the token ID filter
3. `POST /api/sim/start` launches the tick loop in a background thread
4. Dashboard switches to monitor view showing:
   - **Status bar** — run ID, strategy, tick count, pause/stop controls
   - **Stats bar** — balance, unrealized P&L, trade count, position count
   - **Watched Markets** — live price updates via WebSocket
   - **Open Positions** — entry vs current price, P&L
   - **Equity Curve** — Lightweight Charts time series
   - **Trade Feed** — signals, risk verdicts, executed trades

### Event Flow

```
Sim Thread                    EventBus                    WS Manager              Browser
    │                            │                            │                      │
    │── publish(SimEvent) ──────▶│                            │                      │
    │                            │── on_sim_event() ─────────▶│                      │
    │                            │   (call_soon_threadsafe)   │                      │
    │                            │                            │── broadcast(JSON) ──▶│
    │                            │                            │   (WebSocket)        │
```

---

## REST API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Dashboard page (Jinja2 template) |
| `GET` | `/runs` | Run history page |
| `POST` | `/api/scan?strategy=...` | Scan and score markets (returns candidates JSON) |
| `GET` | `/api/sim/state` | Current simulation state (status, ticks, balance, positions, watchlist) |
| `POST` | `/api/sim/start?strategy=...&tick_interval=...` | Start simulation |
| `POST` | `/api/sim/watchlist` | Set watchlist (`{"token_ids": [...]}`) |
| `POST` | `/api/sim/pause` | Pause simulation |
| `POST` | `/api/sim/resume` | Resume simulation |
| `POST` | `/api/sim/stop` | Stop simulation (non-blocking) |
| `GET` | `/api/sim/runs` | List simulation runs |
| `GET` | `/api/sim/trades` | List mock trades |
| `WS` | `/ws/sim` | WebSocket for real-time events |

---

## WebSocket Event Types

| Type | Data Fields | Description |
|---|---|---|
| `sim_status` | `status`, `run_id`, `strategy` | Simulation lifecycle (running/paused/stopped/completed) |
| `tick` | `tick`, `events_count` | Tick heartbeat |
| `events_scanned` | `events[]` | Events fetched this tick |
| `price_update` | `token_id`, `market_id`, `title`, `midpoint`, `spread` | Real-time price for a watched market |
| `signal_emitted` | `strategy`, `market_title`, `side`, `price`, `confidence`, `reasoning` | Strategy produced a trade signal |
| `risk_verdict` | `approved`, `reason`, `side`, `price` | Risk gate decision |
| `trade_executed` | `trade_id`, `side`, `price`, `fill_price`, `size`, `slippage`, `balance_after` | Mock trade executed |
| `position_updated` | `positions[]` | All open positions with current prices |
| `snapshot` | `balance`, `unrealized_pnl`, `realized_pnl`, `open_positions`, `total_trades` | Periodic portfolio snapshot |
| `error` | `error`, `tick` | Error during tick execution |

---

## Configuration Reference

Config is loaded from `polyclaw.config.json` in the project root:

```json
{
  "mode": "mock",
  "mock": {
    "starting_balance": 1000.0,
    "slippage_bps": 10
  },
  "polymarket": {
    "host": "https://clob.polymarket.com",
    "chain_id": 137
  },
  "risk": {
    "max_position_size": 50.0,
    "max_open_positions": 10,
    "max_daily_trades": 20,
    "min_confidence": 0.6
  },
  "simulation": {
    "default_tick_interval_seconds": 30,
    "default_duration_minutes": 240,
    "snapshot_every_n_ticks": 10,
    "record_prices": true,
    "price_db_path": "./data/price_history.db"
  },
  "dashboard": {
    "host": "127.0.0.1",
    "port": 8420,
    "auto_open_browser": true
  },
  "strategies": {
    "sports_volatility": {
      "max_days_to_resolution": 7,
      "min_volume_24hr": 5000,
      "max_spread": 0.06,
      "take_profit_pct": 0.10,
      "stop_loss_pct": 0.15,
      "mean_reversion_threshold": 0.08
    }
  }
}
```

---

## Database Schema

### polyclaw.db (Trade Ledger)

- **trades** — mock and live trade records
- **positions** — open/closed positions
- **snapshots** — periodic portfolio snapshots
- **sim_runs** — simulation run metadata

### price_history.db (Price Recorder)

- **price_ticks** — `(token_id, midpoint, bid, ask, spread, timestamp)`
- **event_metadata** — `(condition_id, event_id, title, question, tags, end_date, token_ids)`
- **scan_sessions** — `(scan_id, strategy, candidates JSON, created_at)`

---

## Performance Optimizations

| Technique | Where | Impact |
|---|---|---|
| Gamma API prices for scan | `SimScheduler._build_scan_context()` | Scan completes in ~1s instead of minutes |
| Batch midpoint API | `PriceEngine.get_midpoints_batch()` | Single CLOB call for all watched tokens |
| Prefetched midpoints in tick | `SimScheduler._execute_tick()` | Avoids per-market CLOB calls during ticks |
| Gamma API response cache | `fetcher._cached_get()` | 60s TTL avoids redundant event fetches |
| Non-blocking stop | `SimScheduler.stop()` | Returns instantly, thread finishes current tick |
| `check_same_thread=False` | SQLite connections | Safe cross-thread access for scan-in-executor |
| CLOB client timeout | `PriceEngine._init_client()` | 5s timeout prevents indefinite hangs |

---

## CLI Commands

```
polyclaw markets [--limit N] [--tag TAG]      # List active markets
polyclaw prices TOKEN_ID                       # Show live price data
polyclaw evaluate [--mode mock|live]           # Show P&L report
polyclaw sim run [--strategy NAME] [--tick N]  # Run simulation headless
polyclaw sim list                              # List past runs
polyclaw sim export [--format csv|json|md]     # Export trades
polyclaw dashboard [--port N] [--no-browser]   # Launch web dashboard
```

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Launch the dashboard
start_dashboard.bat
# Or manually:
python -m polyclaw dashboard

# 3. In the browser:
#    - Click "Scan Markets" to find candidates
#    - Select markets to monitor
#    - Click "Start Monitoring" to begin simulation
```

---

## Tech Stack

- **Python 3.11+** — core runtime
- **FastAPI + Uvicorn** — async web server
- **Jinja2** — server-side HTML templates
- **Pico CSS** — classless dark-theme CSS
- **Lightweight Charts** — equity curve charting
- **WebSocket** — real-time event streaming
- **SQLite** — trade & price persistence
- **py-clob-client** — Polymarket CLOB API SDK
- **Click + Rich** — CLI interface
