---
name: polyclaw
description: Polymarket toolkit — discover markets, get pricing, place orders, and manage positions programmatically.
metadata: {"openclaw": {"requires": {"bins": ["python3"], "env": ["POLYCLAW_MODE"]}, "primaryEnv": "POLYCLAW_PRIVATE_KEY", "emoji": "🦞📈"}}
---

## Polyclaw — Polymarket Toolkit Skill

You can interact with Polymarket prediction markets. Use the polyclaw CLI to discover markets, check prices, place and cancel orders, and manage positions.

### Available Commands

#### Discovery & Pricing
- `polyclaw markets` — List trending active markets sorted by volume.
- `polyclaw market <slug>` — Show details and live prices for a specific market.
- `polyclaw prices <token_id>` — Get current midpoint, bid, ask, spread for a token.
- `polyclaw search --query "<terms>"` — Search markets by keyword.
- `polyclaw resolve <condition_id>` — Check if a market has resolved and show outcome.

#### Trading
- `polyclaw order <token_id> --side BUY --price 0.55 --size 10` — Place a limit order.
- `polyclaw cancel <order_id>` — Cancel a specific open order.
- `polyclaw cancel-all` — Cancel all open orders.

#### Portfolio
- `polyclaw balance` — Show account balance (cash + positions).
- `polyclaw positions` — Show open positions with unrealized P&L.
- `polyclaw status` — Portfolio summary with recent trades.
- `polyclaw report` — Generate P&L report.
- `polyclaw config` — Print current configuration as JSON.

### Workflow

When asked to trade on Polymarket:
1. Run `polyclaw search --query "<topic>"` to find relevant markets.
2. Run `polyclaw market <slug>` to inspect pricing and details.
3. Run `polyclaw prices <token_id>` to get the current orderbook state.
4. Run `polyclaw order <token_id> --side BUY --price <p> --size <s>` to place an order.
5. Run `polyclaw positions` or `polyclaw balance` to monitor the portfolio.

### Configuration

Config file: `{baseDir}/polyclaw.config.json`
Mode is controlled by `POLYCLAW_MODE` env var (default: `mock`).
Live trading requires `POLYCLAW_PRIVATE_KEY` and `POLYCLAW_FUNDER_ADDRESS`.
