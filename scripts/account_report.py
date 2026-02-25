#!/usr/bin/env python3
"""Generate a detailed Polymarket account report.

Run:
    python scripts/account_report.py
    python scripts/account_report.py --detailed      # include positions & market breakdown
    python scripts/account_report.py --trades        # include full trade log (implies --detailed)
    python scripts/account_report.py --json          # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── ensure project root is importable ──────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

CLOB_HOST = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CHAIN_ID = 137

# ── ANSI colours ───────────────────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _colour_pnl(value: float, fmt: str = "+,.2f") -> str:
    """Return a coloured P&L string (green positive, red negative)."""
    colour = GREEN if value >= 0 else RED
    return f"{colour}${value:{fmt}}{RESET}"


def _ts(epoch: int | float) -> str:
    """Format a UNIX timestamp as a readable date."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _ts_short(epoch: int | float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")


# ── Data fetchers ──────────────────────────────────────────────────

def fetch_cash_balance(pk: str, funder: str) -> float | None:
    """Get available-to-trade USDC via CLOB API (proxy wallet)."""
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import BalanceAllowanceParams

        sig_type = 1 if funder else 0
        client = ClobClient(
            CLOB_HOST, key=pk, chain_id=CHAIN_ID,
            funder=funder or None, signature_type=sig_type,
        )
        creds = client.derive_api_key()
        client = ClobClient(
            CLOB_HOST, key=pk, chain_id=CHAIN_ID,
            funder=funder or None, signature_type=sig_type, creds=creds,
        )
        bal = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type="COLLATERAL", signature_type=sig_type)
        )
        if bal and isinstance(bal, dict):
            return float(bal.get("balance", "0")) / 1e6
    except Exception as exc:
        print(f"  {YELLOW}Could not fetch CLOB balance: {exc}{RESET}", file=sys.stderr)
    return None


def fetch_positions(funder: str) -> list[dict]:
    """Fetch all positions from the Data API."""
    import requests
    try:
        resp = requests.get(
            f"{DATA_API}/positions",
            params={"user": funder, "sizeThreshold": "0"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def fetch_activity(funder: str) -> list[dict]:
    """Fetch full activity history (trades + redemptions)."""
    import requests
    try:
        resp = requests.get(
            f"{DATA_API}/activity",
            params={"user": funder, "limit": 10000},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def fetch_trades(funder: str) -> list[dict]:
    """Fetch trade history."""
    import requests
    try:
        resp = requests.get(
            f"{DATA_API}/trades",
            params={"user": funder, "limit": 10000},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def fetch_positions_value(funder: str) -> float | None:
    """Fetch aggregate positions value from the Data API."""
    import requests
    try:
        resp = requests.get(
            f"{DATA_API}/value",
            params={"user": funder},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                data = data[0]
            if isinstance(data, dict) and data.get("value") is not None:
                return float(data["value"])
    except Exception:
        pass
    return None


# ── Report builder ─────────────────────────────────────────────────

def build_report(
    pk: str,
    funder: str,
    include_trades: bool = False,
) -> dict:
    """Build the full account report as a dict."""

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "funder_address": funder,
    }

    # ── Cash balance ──
    cash = fetch_cash_balance(pk, funder)
    report["cash_balance"] = cash

    # ── Positions value ──
    positions_value = fetch_positions_value(funder)
    report["positions_value"] = positions_value

    # ── Portfolio total ──
    if cash is not None and positions_value is not None:
        report["portfolio_total"] = cash + positions_value
    else:
        report["portfolio_total"] = None

    # ── Positions breakdown ──
    positions = fetch_positions(funder)
    open_pos = [p for p in positions if not p.get("redeemable", False)]
    settled_pos = [p for p in positions if p.get("redeemable", False)]

    report["open_positions"] = []
    for p in open_pos:
        report["open_positions"].append({
            "title": p.get("title", "?"),
            "outcome": p.get("outcome", "?"),
            "shares": float(p.get("size", 0) or 0),
            "avg_price": float(p.get("avgPrice", 0) or 0),
            "cur_price": float(p.get("curPrice", 0) or 0),
            "cost": float(p.get("initialValue", 0) or 0),
            "current_value": float(p.get("currentValue", 0) or 0),
            "pnl": float(p.get("cashPnl", 0) or 0),
            "pnl_pct": float(p.get("percentPnl", 0) or 0),
            "end_date": p.get("endDate", ""),
        })

    report["settled_positions"] = []
    for p in settled_pos:
        report["settled_positions"].append({
            "title": p.get("title", "?"),
            "outcome": p.get("outcome", "?"),
            "shares": float(p.get("size", 0) or 0),
            "cost": float(p.get("initialValue", 0) or 0),
            "pnl": float(p.get("cashPnl", 0) or 0),
            "pnl_pct": float(p.get("percentPnl", 0) or 0),
        })

    # ── Activity summary ──
    activities = fetch_activity(funder)
    trades_data = [a for a in activities if a.get("type") == "TRADE"]
    redeems_data = [a for a in activities if a.get("type") == "REDEEM"]

    buys = [t for t in trades_data if t.get("side") == "BUY"]
    sells = [t for t in trades_data if t.get("side") == "SELL"]

    total_bought = sum(
        float(t.get("size", 0) or 0) * float(t.get("price", 0) or 0)
        for t in buys
    )
    total_sold = sum(
        float(t.get("size", 0) or 0) * float(t.get("price", 0) or 0)
        for t in sells
    )
    total_redeemed = sum(float(r.get("usdcSize", 0) or 0) for r in redeems_data)

    report["trading_summary"] = {
        "total_trades": len(trades_data),
        "total_buys": len(buys),
        "total_sells": len(sells),
        "total_redemptions": len(redeems_data),
        "total_spent_buying": total_bought,
        "total_received_selling": total_sold,
        "total_redeemed": total_redeemed,
        "net_trading_pnl": total_sold + total_redeemed - total_bought,
    }

    # ── Time range ──
    timestamps = [a.get("timestamp", 0) for a in activities if a.get("timestamp")]
    if timestamps:
        report["first_activity"] = min(timestamps)
        report["last_activity"] = max(timestamps)
        report["active_days"] = (max(timestamps) - min(timestamps)) / 86400
    else:
        report["first_activity"] = None
        report["last_activity"] = None
        report["active_days"] = 0

    # ── Markets traded ──
    by_market: dict[str, dict] = {}
    for t in trades_data:
        title = t.get("title", "Unknown")
        if title not in by_market:
            by_market[title] = {
                "title": title,
                "slug": t.get("slug", ""),
                "buys": 0,
                "sells": 0,
                "spent": 0.0,
                "received": 0.0,
                "redeemed": 0.0,
            }
        usdc = float(t.get("size", 0) or 0) * float(t.get("price", 0) or 0)
        if t.get("side") == "BUY":
            by_market[title]["buys"] += 1
            by_market[title]["spent"] += usdc
        elif t.get("side") == "SELL":
            by_market[title]["sells"] += 1
            by_market[title]["received"] += usdc

    # Add redemption amounts to their markets
    for r in redeems_data:
        title = r.get("title", "Unknown")
        if title in by_market:
            by_market[title]["redeemed"] += float(r.get("usdcSize", 0) or 0)
        else:
            by_market[title] = {
                "title": title,
                "slug": r.get("slug", ""),
                "buys": 0,
                "sells": 0,
                "spent": 0.0,
                "received": 0.0,
                "redeemed": float(r.get("usdcSize", 0) or 0),
            }

    for m in by_market.values():
        m["net"] = m["received"] + m["redeemed"] - m["spent"]

    report["markets"] = list(by_market.values())

    # ── Win/loss stats ──
    wins = sum(1 for m in by_market.values() if m["net"] > 0.005)
    losses = sum(1 for m in by_market.values() if m["net"] < -0.005)
    breakeven = len(by_market) - wins - losses
    report["win_loss"] = {
        "markets_traded": len(by_market),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": wins / max(wins + losses, 1),
    }

    # ── Optional: full trade log ──
    if include_trades:
        all_trades = fetch_trades(funder)
        all_trades.sort(key=lambda t: t.get("timestamp", 0), reverse=True)
        report["trade_log"] = [
            {
                "timestamp": t.get("timestamp", 0),
                "date": _ts(t["timestamp"]) if t.get("timestamp") else "",
                "side": t.get("side", ""),
                "title": t.get("title", "?"),
                "outcome": t.get("outcome", "?"),
                "shares": float(t.get("size", 0) or 0),
                "price": float(t.get("price", 0) or 0),
                "usdc": float(t.get("size", 0) or 0) * float(t.get("price", 0) or 0),
            }
            for t in all_trades
        ]

    # ── Estimated initial deposit ──
    # Heuristic: cash_now + total_spent - total_sold - total_redeemed + open_positions_cost
    open_cost = sum(p["cost"] for p in report["open_positions"])
    if cash is not None:
        report["estimated_deposit"] = cash + total_bought - total_sold - total_redeemed + open_cost
    else:
        report["estimated_deposit"] = None

    return report


# ── Pretty printer ─────────────────────────────────────────────────

def print_report(report: dict, *, detailed: bool = False) -> None:
    """Print the report in human-readable format."""

    print()
    print(f"{BOLD}{'=' * 62}{RESET}")
    print(f"{BOLD}  Polyclaw — Account Report{RESET}")
    print(f"{BOLD}{'=' * 62}{RESET}")
    print(f"  {DIM}Generated: {report['generated_at']}{RESET}")
    print(f"  {DIM}Account:   {report['funder_address']}{RESET}")

    if report.get("first_activity"):
        print(f"  {DIM}Active:    {_ts_short(report['first_activity'])} → {_ts_short(report['last_activity'])}  ({report['active_days']:.0f} days){RESET}")

    # ── Portfolio Overview ──
    print(f"\n{BOLD}  PORTFOLIO OVERVIEW{RESET}")
    print(f"  {'─' * 40}")
    cash = report.get("cash_balance")
    pos_val = report.get("positions_value")
    total = report.get("portfolio_total")
    est_deposit = report.get("estimated_deposit")

    if cash is not None:
        print(f"  Available to trade:  {BOLD}${cash:,.2f}{RESET}")
    if pos_val is not None:
        print(f"  Positions value:     {BOLD}${pos_val:,.2f}{RESET}")
    if total is not None:
        print(f"  Portfolio total:     {BOLD}${total:,.2f}{RESET}")
    if est_deposit is not None:
        print(f"  Est. total deposited:${est_deposit:,.2f}")

    ts = report.get("trading_summary", {})
    net = ts.get("net_trading_pnl", 0)
    if est_deposit and est_deposit > 0 and total is not None:
        overall_pnl = total - est_deposit
        overall_pct = (overall_pnl / est_deposit) * 100
        print(f"  Overall return:      {_colour_pnl(overall_pnl)} ({overall_pct:+.1f}%)")

    # ── Trading Activity ──
    print(f"\n{BOLD}  TRADING ACTIVITY{RESET}")
    print(f"  {'─' * 40}")
    print(f"  Total trades:        {ts.get('total_trades', 0)}")
    print(f"    Buys:              {ts.get('total_buys', 0)}   (${ts.get('total_spent_buying', 0):,.2f})")
    print(f"    Sells:             {ts.get('total_sells', 0)}   (${ts.get('total_received_selling', 0):,.2f})")
    print(f"  Redemptions:         {ts.get('total_redemptions', 0)}   (${ts.get('total_redeemed', 0):,.2f})")
    print(f"  Net trading P&L:     {_colour_pnl(net)}")

    # ── Win/Loss ──
    wl = report.get("win_loss", {})
    print(f"\n{BOLD}  WIN / LOSS{RESET}")
    print(f"  {'─' * 40}")
    print(f"  Markets traded:      {wl.get('markets_traded', 0)}")
    print(f"  Wins:                {GREEN}{wl.get('wins', 0)}{RESET}")
    print(f"  Losses:              {RED}{wl.get('losses', 0)}{RESET}")
    print(f"  Breakeven:           {wl.get('breakeven', 0)}")
    print(f"  Win rate:            {wl.get('win_rate', 0) * 100:.0f}%")

    if not detailed:
        n_open = len(report.get("open_positions", []))
        n_settled = len(report.get("settled_positions", []))
        if n_open or n_settled:
            print(f"\n  {DIM}Use --detailed to see {n_open} open and {n_settled} settled positions, market breakdown{RESET}")

    # ── Open Positions ──
    open_pos = report.get("open_positions", [])
    if detailed and open_pos:
        print(f"\n{BOLD}  OPEN POSITIONS ({len(open_pos)}){RESET}")
        print(f"  {'─' * 58}")
        for p in open_pos:
            pnl_colour = GREEN if p["pnl"] >= 0 else RED
            print(
                f"  {p['outcome']:3s} {p['shares']:.1f}sh "
                f"{p['avg_price']*100:.0f}c->{p['cur_price']*100:.0f}c  "
                f"cost ${p['cost']:.2f}  val ${p['current_value']:.2f}  "
                f"{pnl_colour}{p['pnl']:+.2f} ({p['pnl_pct']:+.1f}%){RESET}"
            )
            print(f"    {DIM}{p['title'][:60]}{RESET}")
            if p.get("end_date"):
                print(f"    {DIM}Ends: {p['end_date']}{RESET}")
        open_cost = sum(p["cost"] for p in open_pos)
        open_pnl = sum(p["pnl"] for p in open_pos)
        print(f"  {'─' * 58}")
        print(f"  Total cost: ${open_cost:,.2f}   Unrealized P&L: {_colour_pnl(open_pnl)}")

    # ── Settled Positions ──
    settled = report.get("settled_positions", [])
    if detailed and settled:
        print(f"\n{BOLD}  SETTLED POSITIONS ({len(settled)}){RESET}")
        print(f"  {'─' * 58}")
        for p in settled:
            pnl_colour = GREEN if p["pnl"] >= 0 else RED
            print(
                f"  {p['outcome']:3s} {p['shares']:.1f}sh  "
                f"cost ${p['cost']:.2f}  "
                f"{pnl_colour}P&L {p['pnl']:+.2f} ({p['pnl_pct']:+.1f}%){RESET}  "
                f"{DIM}{p['title'][:40]}{RESET}"
            )
        settled_cost = sum(p["cost"] for p in settled)
        settled_pnl = sum(p["pnl"] for p in settled)
        print(f"  {'─' * 58}")
        print(f"  Total cost: ${settled_cost:,.2f}   Realized P&L: {_colour_pnl(settled_pnl)}")

    # ── Per-Market Breakdown ──
    markets = report.get("markets", [])
    if detailed and markets:
        markets_sorted = sorted(markets, key=lambda m: m["net"], reverse=True)
        print(f"\n{BOLD}  MARKET BREAKDOWN{RESET}")
        print(f"  {'─' * 58}")
        for m in markets_sorted:
            net_colour = GREEN if m["net"] >= 0 else RED
            parts = []
            if m["spent"] > 0:
                parts.append(f"bought ${m['spent']:.2f}")
            if m["received"] > 0:
                parts.append(f"sold ${m['received']:.2f}")
            if m["redeemed"] > 0:
                parts.append(f"redeemed ${m['redeemed']:.2f}")
            detail = ", ".join(parts)
            print(f"  {net_colour}Net {m['net']:+.2f}{RESET}  {detail}")
            print(f"    {DIM}{m['title'][:58]}{RESET}")

    # ── Trade Log ──
    trade_log = report.get("trade_log", [])
    if trade_log:
        print(f"\n{BOLD}  TRADE LOG ({len(trade_log)} trades){RESET}")
        print(f"  {'─' * 58}")
        print(f"  {'Date':<22s} {'Side':<5s} {'Shares':>7s} {'Price':>7s} {'USDC':>8s}  Market")
        print(f"  {'─' * 58}")
        for t in trade_log:
            side_colour = GREEN if t["side"] == "SELL" else CYAN
            print(
                f"  {t['date']:<22s} "
                f"{side_colour}{t['side']:<5s}{RESET} "
                f"{t['shares']:>7.2f} "
                f"{t['price']:>6.2f}c  "
                f"${t['usdc']:>7.2f}  "
                f"{DIM}{t['title'][:30]}{RESET}"
            )

    print(f"\n{BOLD}{'=' * 62}{RESET}")
    print()


# ── Main ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket account report")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--detailed", "-d", action="store_true", help="Show positions and market breakdown")
    parser.add_argument("--trades", action="store_true", help="Include full trade log (implies --detailed)")
    args = parser.parse_args()

    # --trades implies --detailed
    if args.trades:
        args.detailed = True

    # Load env
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)
    else:
        load_dotenv()

    pk = os.environ.get("POLYCLAW_PRIVATE_KEY", "").strip()
    funder = os.environ.get("POLYCLAW_FUNDER_ADDRESS", "").strip()

    if not pk:
        print("ERROR: POLYCLAW_PRIVATE_KEY not set", file=sys.stderr)
        sys.exit(1)
    if not funder:
        print("ERROR: POLYCLAW_FUNDER_ADDRESS not set", file=sys.stderr)
        sys.exit(1)

    report = build_report(pk, funder, include_trades=args.trades)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_report(report, detailed=args.detailed)


if __name__ == "__main__":
    main()
