# Sports Volatility Strategy

> **Name:** `sports_volatility`
> **Module:** `polyclaw/strategies/sports_volatility.py`
> **Type:** Mean-reversion, intra-day frequency

---

## Summary

The Sports Volatility Strategy targets **sports betting markets** on Polymarket that resolve within a few days and exhibit high price volatility. It uses a **mean-reversion** approach: buying when the price dips significantly below its recent average, and selling when a profit target or stop-loss is hit.

---

## How It Works

### 1. Market Filtering

Every tick, the strategy filters incoming markets against these criteria:

| Filter | Default | Description |
|---|---|---|
| **Tags** | `sports, nba, nfl, mlb, nhl, soccer, mma, tennis, boxing, cricket, f1, rugby` | Event must have at least one matching tag |
| **Time to Resolution** | ≤ 7 days | Only events resolving within `max_days_to_resolution` days. Events past their end date are skipped. |
| **24h Volume** | ≥ $5,000 | Minimum trading volume (from event or market-level) |
| **Spread** | ≤ 0.06 | Maximum bid-ask spread — filters out illiquid markets |

### 2. Price Tracking

For each qualifying market, the strategy maintains a **rolling price window** (default 20 data points) per token ID. On each tick:

1. The current midpoint is appended to the window
2. If the window exceeds `price_window_size`, old values are trimmed

### 3. Volatility Calculation

Volatility is measured as the **coefficient of variation** (CV):

$$
\text{volatility} = \frac{\sigma(\text{prices})}{\mu(\text{prices})}
$$

- Requires at least 3 data points
- Must exceed `min_volatility` threshold (default 0.03) to generate a signal

### 4. Entry Signal (Buy)

A **BUY signal** is generated when:

$$
\text{midpoint} < \mu(\text{prices}) \times (1 - \text{mean\_reversion\_threshold})
$$

With default threshold of 0.08, this means the price must dip **8% below the recent mean**.

When triggered:
- **Confidence** = `min(0.9, 0.6 + volatility)` — higher volatility → higher confidence
- **Position size** = `min(20, max(5, 10 × confidence))` — $5 to $20 per trade
- Outcome is always `"Yes"` (betting on the Yes token)

### 5. Exit Signal (Sell)

Open positions are checked for exit on every tick:

| Condition | Default | Action |
|---|---|---|
| **Take Profit** | +10% | Close when `(current - entry) / entry ≥ 0.10` |
| **Stop Loss** | -15% | Close when `(current - entry) / entry ≤ -0.15` |
| **Near Resolution** | < 1 hour | Close before market resolves to avoid resolution risk |

---

## Scan Scoring

During the scan phase (before monitoring starts), the strategy scores all qualifying markets with a composite score (0–100):

$$
\text{score} = \text{vol\_score} \times 40 + \text{spread\_score} \times 30 + \text{volume\_score} \times 30
$$

| Component | Calculation | Weight |
|---|---|---|
| **Volatility Score** | CV from price history (0 on first scan since no history yet) | 40% |
| **Spread Score** | `max(0, 1 - spread / 0.10)` — tighter spread scores higher | 30% |
| **Volume Score** | `min(1.0, volume_24hr / 100,000)` — capped at $100k | 30% |

> **Note:** On the first scan, all candidates will have the same score because there is no price history yet. As the simulation runs and collects price data, rescanning will produce differentiated scores.

---

## Configuration

All parameters can be overridden in `polyclaw.config.json` under `strategies.sports_volatility`:

```json
{
  "strategies": {
    "sports_volatility": {
      "max_days_to_resolution": 7,
      "min_volatility": 0.03,
      "max_spread": 0.06,
      "price_window_size": 20,
      "min_volume_24hr": 5000,
      "tags": ["sports", "nba", "nfl", "mlb", "nhl", "soccer",
               "mma", "tennis", "boxing", "cricket", "f1", "rugby"],
      "take_profit_pct": 0.10,
      "stop_loss_pct": 0.15,
      "mean_reversion_threshold": 0.08
    }
  }
}
```

### Parameter Reference

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_days_to_resolution` | int | 7 | Maximum days until market resolves |
| `min_volatility` | float | 0.03 | Minimum CV to trigger entry signal |
| `max_spread` | float | 0.06 | Maximum bid-ask spread (0.06 = 6 cents) |
| `price_window_size` | int | 20 | Rolling window size for price history |
| `min_volume_24hr` | float | 5000 | Minimum 24h volume in USD |
| `tags` | list[str] | *(see above)* | Allowed event tags (case-insensitive) |
| `take_profit_pct` | float | 0.10 | Take profit threshold (10%) |
| `stop_loss_pct` | float | 0.15 | Stop loss threshold (15%) |
| `mean_reversion_threshold` | float | 0.08 | Price must dip this % below mean to buy |

---

## Risk Integration

Before any trade is executed, the signal passes through the **RiskGate** which enforces:

1. **Confidence ≥ 0.6** — signal must meet minimum confidence
2. **Trade cost ≤ $50** — single position cap (`max_position_size`)
3. **Open positions ≤ 10** — maximum concurrent positions
4. **Daily trades ≤ 20** — rate limiting
5. **Sufficient balance** — can't spend more than available

If any check fails, the signal is rejected and a `risk_verdict` event is published to the dashboard.

---

## Signal Flow Diagram

```
Tick
 │
 ├─ Fetch events from Gamma API
 │
 ├─ For each market in watchlist:
 │   │
 │   ├─ Build MarketContext (midpoint, spread, volume, time-to-resolution)
 │   │
 │   ├─ Strategy.evaluate(context):
 │   │   ├─ _passes_filters() → tag, time, volume, spread checks
 │   │   ├─ _update_price_history() → rolling window
 │   │   ├─ _get_volatility() → CV check
 │   │   └─ Mean reversion check → TradeSignal or None
 │   │
 │   └─ If signal:
 │       ├─ RiskGate.check(signal) → approved/rejected
 │       └─ MockExecutor.execute(signal) → fill + balance update
 │
 └─ Check exits for open positions:
     ├─ Take profit (+10%)
     ├─ Stop loss (-15%)
     └─ Near resolution (< 1h)
```

---

## Considerations & Limitations

- **No live execution** — all trades are paper-traded through `MockExecutor`
- **Single-sided** — only buys Yes tokens (no short selling or No token trading)
- **No orderbook depth analysis** — uses midpoint only, not full book
- **Slippage is simulated** — fixed basis points (default 10 bps), not market-impact modeled
- **Price history resets on restart** — rolling window is in-memory only
- **Mean reversion assumption** — may not hold for events driven by breaking news (e.g., injuries, disqualifications)

---

## Writing a New Strategy

To create a custom strategy, subclass `BaseStrategy`:

```python
from polyclaw.strategy import BaseStrategy
from polyclaw.models import MarketContext, Position, TradeSignal

class MyStrategy(BaseStrategy):
    name = "my_strategy"
    description = "My custom strategy"

    def configure(self, config):
        self.params = config.strategies.get("my_strategy", {})

    def evaluate(self, context: MarketContext) -> TradeSignal | None:
        # Return a TradeSignal to enter, or None to skip
        ...

    def should_close(self, position: Position, context: MarketContext) -> TradeSignal | None:
        # Return a TradeSignal to exit, or None to hold
        ...

    def scan_candidates(self, contexts: list[MarketContext]) -> list[dict]:
        # Score and filter markets for the scan phase
        ...
```

Register it in `polyclaw/dashboard/app.py`:

```python
from my_module import MyStrategy

my_strat = MyStrategy()
my_strat.configure(config)
registry.register(my_strat)
```
