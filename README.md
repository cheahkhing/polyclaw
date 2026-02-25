# Polyclaw

**Polymarket toolkit for [OpenClaw](https://github.com/openclaw/openclaw) agents.**

Polyclaw provides reusable tools and utilities for browsing, querying, pricing, and trading on [Polymarket](https://polymarket.com). It exposes a programmatic Python API and a CLI — designed to be consumed by OpenClaw agents that implement their own trading strategies.

---

## Features

- **Market Discovery** — fetch and filter active events from the Gamma API, search by keyword or tag
- **Real-Time Pricing** — midpoints, spreads, and orderbook depth via the CLOB API (SDK + REST fallback)
- **WebSocket Streaming** — persistent connections to Market, User, Sports, and RTDS channels with auto-reconnect
- **Mock & Live Execution** — paper-trade with simulated fills and slippage, or place real orders with `py-clob-client`
- **SQLite Trade Ledger** — full trade history, position tracking, portfolio snapshots
- **P&L Reporting** — win rate, total P&L, max drawdown, per-strategy breakdown
- **Rich CLI** — coloured tables, summary views, config inspection

---

## Project Structure

```
polyclaw/
├── polyclaw/
│   ├── __init__.py
│   ├── __main__.py             # python -m polyclaw entrypoint
│   ├── cli.py                  # Click CLI commands
│   ├── config.py               # Hierarchical dataclass config + JSON loader
│   ├── models.py               # Market + trading data models
│   ├── fetcher.py              # Gamma API market discovery
│   ├── pricer.py               # CLOB API pricing engine
│   ├── streaming.py            # WebSocket manager (4 channels)
│   ├── executor.py             # Live trade executor (py-clob-client L2 auth)
│   ├── mock_executor.py        # Paper-trade simulator
│   ├── ledger.py               # SQLite trade journal
│   ├── evaluator.py            # P&L scoring and reports
│   └── utils/
│       ├── logging.py
│       └── formatting.py       # Rich table formatters
├── scripts/
│   ├── account_report.py       # Standalone account report
│   └── check_setup.py          # Setup verification
├── tests/
├── polyclaw.config.json        # Default configuration
├── requirements.txt
├── requirements-dev.txt
├── setup.py
└── skill/
    └── polyclaw/
        └── SKILL.md            # OpenClaw skill definition
```

---

## Quick Start

### 1. Clone & create virtual environment

```bash
git clone <repo-url> polyclaw
cd polyclaw
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements-dev.txt
pip install -e .
```

### 3. Run it

```bash
# List trending markets
python -m polyclaw markets

# Search for specific topics
python -m polyclaw search --query "bitcoin"

# View a specific market
python -m polyclaw market <slug>

# View open positions
python -m polyclaw positions

# P&L report
python -m polyclaw report

# Portfolio status
python -m polyclaw status

# Show config
python -m polyclaw config

# Place a limit order (mock mode by default)
python -m polyclaw order <token_id> --side BUY --price 0.55 --size 10

# Cancel an order
python -m polyclaw cancel <order_id>

# Cancel all open orders
python -m polyclaw cancel-all

# Check if a market resolved
python -m polyclaw resolve <condition_id>

# Show account balance
python -m polyclaw balance
```

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `markets` | List trending active markets sorted by 24h volume |
| `market <slug>` | Show details and live prices for a specific market |
| `prices <token_id>` | Show midpoint, bid, ask, spread for a token |
| `search --query "..."` | Search markets by keyword |
| `positions` | Show open positions with unrealized P&L |
| `report` | Generate P&L report |
| `status` | Portfolio balance, recent trades summary |
| `config` | Print current configuration as JSON |
| `order <token_id>` | Place a limit/market order (see flags below) |
| `cancel <order_id>` | Cancel a specific open order |
| `cancel-all` | Cancel all open orders |
| `resolve <condition_id>` | Check if a market has resolved and show outcome |
| `balance` | Show Polymarket account balance (cash + positions) |

**Global options:**

```bash
python -m polyclaw --config path/to/config.json <command>
python -m polyclaw --mode live <command>
```

---

## Programmatic API (for agents)

Polyclaw is designed to be imported by OpenClaw agents:

```python
from polyclaw.config import load_config
from polyclaw.fetcher import MarketFetcher
from polyclaw.pricer import PriceEngine
from polyclaw.executor import TradeExecutor
from polyclaw.mock_executor import MockExecutor
from polyclaw.ledger import TradeLedger
from polyclaw.streaming import WebSocketManager
from polyclaw.models import TradeSignal, MarketContext

cfg = load_config()

# Browse markets
fetcher = MarketFetcher(cfg)
events = fetcher.get_active_events(limit=10)
event = fetcher.get_event_by_slug("some-market-slug")

# Get prices
pricer = PriceEngine(cfg)
mid = pricer.get_midpoint(token_id)
spread = pricer.get_spread(token_id)
book = pricer.get_orderbook(token_id)
batch = pricer.get_midpoints_batch([tid1, tid2, tid3])

# Execute trades (agent decides what to trade)
signal = TradeSignal(
    market_id="...", token_id="...",
    side="BUY", outcome="Yes",
    price=0.45, size=10.0,
    confidence=0.8, reasoning="agent's reasoning",
    strategy="my_agent_strategy",
)
executor = TradeExecutor(cfg)  # or MockExecutor(cfg)
result = executor.execute(signal, context)

# Track trades
ledger = TradeLedger(cfg)
ledger.record_trade(signal, mode="mock", fill_price=0.46)
trades = ledger.get_trades(mode="mock")
positions = ledger.get_open_positions()

# Stream prices
ws = WebSocketManager(cfg)
ws.on("price_change", my_callback)
await ws.start(token_ids=[...])
```

---

## Configuration

Polyclaw loads config from (in order of priority):

1. `--config` CLI argument
2. `POLYCLAW_CONFIG` environment variable
3. `polyclaw.config.json` in the current directory
4. Built-in defaults

### Key settings in `polyclaw.config.json`

```jsonc
{
  "mode": "mock",                      // "mock" (paper) or "live" (real orders)

  "mock": {
    "starting_balance": 1000.0,        // Starting paper balance in USD
    "slippage_bps": 10                 // Simulated slippage in basis points
  },

  "risk": {
    "max_position_size": 50.0,         // Max USD per position
    "max_open_positions": 10,
    "max_daily_trades": 20,
    "min_confidence": 0.6              // Minimum signal confidence to execute
  },

  "filters": {
    "min_volume_24hr": 1000,           // Skip low-volume markets
    "min_liquidity": 5000,
    "tags_include": [],                // Only include these tags (empty = all)
    "tags_exclude": []                 // Exclude these tags
  }
}
```

See the full default config in [polyclaw.config.json](polyclaw.config.json).

---

## Authentication

### Mock mode (default)

No credentials needed. All trades are simulated locally.

### Live mode

Live trading requires a Polygon wallet. Set credentials via environment variables or a `.env` file:

```env
POLYCLAW_PRIVATE_KEY=0xYourPolygonPrivateKey
POLYCLAW_FUNDER_ADDRESS=0xYourWalletAddress
```

The `.env` file is loaded automatically by `python-dotenv` and is excluded from git via `.gitignore`.

Then switch to live mode:

```bash
python -m polyclaw --mode live status
```

> **Warning:** Live mode places real orders with real money. Use at your own risk.

---

## Testing

```bash
# Run the full test suite
python -m pytest tests/ -v

# Run with coverage (install pytest-cov first)
python -m pytest tests/ --cov=polyclaw --cov-report=term-missing
```

---

## Polymarket APIs Used

| API | Base URL | Auth | Used For |
|-----|----------|------|----------|
| **Gamma API** | `https://gamma-api.polymarket.com` | None | Market/event discovery, tags, search |
| **CLOB API** | `https://clob.polymarket.com` | Public + L2 for trading | Orderbook, pricing, order placement |
| **Data API** | `https://data-api.polymarket.com` | None | User positions, activity |

**WebSocket Channels:**

| Channel | Endpoint | Purpose |
|---------|----------|---------|
| Market | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | Real-time prices, book updates |
| User | `wss://ws-subscriptions-clob.polymarket.com/ws/user` | Order/trade lifecycle (L2 auth) |
| Sports | `wss://sports-api.polymarket.com/ws` | Live game scores |
| RTDS | `wss://ws-live-data.polymarket.com` | Crypto prices, comments |

---

## OpenClaw Integration

Polyclaw includes a skill package in the `skill/polyclaw/` directory. See [skill/polyclaw/SKILL.md](skill/polyclaw/SKILL.md) for the full skill definition.

---

## Data Persistence

All trade history is stored in a SQLite database (default: `./data/polyclaw.db`). Change the path in config:

```json
"database": {
    "path": "./data/polyclaw.db"
}
```

---

## License

This is a proof-of-concept project. Use at your own risk. Not financial advice.
