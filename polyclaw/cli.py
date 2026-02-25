"""CLI Interface — entry point for Polyclaw toolkit."""

from __future__ import annotations

import sys

import click
from rich.console import Console

from polyclaw.config import load_config, PolyclawConfig
from polyclaw.utils.logging import setup_logging, get_logger

console = Console()
logger = get_logger("cli")


def _get_config(ctx: click.Context) -> PolyclawConfig:
    """Retrieve or create the config from Click context."""
    cfg = ctx.obj
    if cfg is None:
        cfg = load_config()
        ctx.obj = cfg
    return cfg


@click.group()
@click.option("--config", "config_path", default=None, help="Path to config file")
@click.option("--mode", default=None, help="Execution mode: mock or live")
@click.pass_context
def main(ctx: click.Context, config_path: str | None, mode: str | None) -> None:
    """Polyclaw — Polymarket toolkit for OpenClaw agents."""
    cfg = load_config(config_path)
    if mode:
        cfg.mode = mode
    ctx.ensure_object(dict)
    ctx.obj = cfg
    setup_logging(cfg.log_level)


@main.command()
@click.option("--limit", default=20, help="Number of markets to show")
@click.option("--tag", default=None, help="Filter by tag")
@click.pass_context
def markets(ctx: click.Context, limit: int, tag: str | None) -> None:
    """List trending active markets sorted by volume."""
    cfg = _get_config(ctx)

    from polyclaw.fetcher import MarketFetcher
    from polyclaw.utils.formatting import format_markets_table

    fetcher = MarketFetcher(cfg)
    console.print("[bold]Fetching active markets...[/bold]")

    try:
        events = fetcher.get_active_events(limit=limit, tag=tag)
        if not events:
            console.print("[yellow]No markets found matching filters.[/yellow]")
            return
        console.print(format_markets_table(events))
        console.print(f"\n[dim]Showing {len(events)} events[/dim]")
    except Exception as exc:
        console.print(f"[red]Error fetching markets: {exc}[/red]")


@main.command()
@click.argument("slug")
@click.pass_context
def market(ctx: click.Context, slug: str) -> None:
    """Show details and prices for a specific market by slug."""
    cfg = _get_config(ctx)

    from polyclaw.fetcher import MarketFetcher
    from polyclaw.pricer import PriceEngine
    from polyclaw.utils.formatting import format_market_detail

    fetcher = MarketFetcher(cfg)
    pricer = PriceEngine(cfg)

    console.print(f"[bold]Fetching market: {slug}[/bold]")

    try:
        event = fetcher.get_event_by_slug(slug)
        if not event:
            console.print(f"[red]Market '{slug}' not found.[/red]")
            return

        # Enrich with live prices
        for m in event.markets:
            if m.token_id_yes:
                try:
                    mid = pricer.get_midpoint(m.token_id_yes)
                    spread_data = pricer.get_spread(m.token_id_yes)
                    console.print(
                        f"  [dim]{m.question[:50]}[/dim] — "
                        f"Mid: ${mid:.4f}, Spread: ${spread_data['spread']:.4f}"
                    )
                except Exception:
                    pass

        console.print(format_market_detail(event))
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")


@main.command()
@click.argument("token_id")
@click.pass_context
def prices(ctx: click.Context, token_id: str) -> None:
    """Show current midpoint, bid, ask for a token."""
    cfg = _get_config(ctx)

    from polyclaw.pricer import PriceEngine

    pricer = PriceEngine(cfg)

    try:
        mid = pricer.get_midpoint(token_id)
        spread = pricer.get_spread(token_id)
        last = pricer.get_last_trade_price(token_id)

        console.print(f"[bold]Token:[/bold] {token_id[:30]}…")
        console.print(f"  Midpoint:    ${mid:.4f}")
        console.print(f"  Best Bid:    ${spread['bid']:.4f}")
        console.print(f"  Best Ask:    ${spread['ask']:.4f}")
        console.print(f"  Spread:      ${spread['spread']:.4f}")
        console.print(f"  Last Trade:  ${last:.4f}")
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")


@main.command()
@click.pass_context
def positions(ctx: click.Context) -> None:
    """Show current open positions and unrealized P&L."""
    cfg = _get_config(ctx)

    from polyclaw.ledger import TradeLedger
    from polyclaw.pricer import PriceEngine
    from polyclaw.utils.formatting import format_positions_table

    ledger = TradeLedger(cfg)
    pricer = PriceEngine(cfg)

    open_pos = ledger.get_open_positions()

    if not open_pos:
        console.print("[yellow]No open positions.[/yellow]")
        ledger.close()
        return

    # Update prices
    for pos in open_pos:
        try:
            pos.current_price = pricer.get_midpoint(pos.token_id)
            pos.unrealized_pnl = (pos.current_price - pos.entry_price) * pos.size
        except Exception:
            pass

    console.print(format_positions_table(open_pos))
    ledger.close()


@main.command()
@click.pass_context
def report(ctx: click.Context) -> None:
    """Show P&L report."""
    cfg = _get_config(ctx)

    from polyclaw.ledger import TradeLedger
    from polyclaw.pricer import PriceEngine
    from polyclaw.evaluator import Evaluator
    from polyclaw.utils.formatting import format_evaluation_report

    ledger = TradeLedger(cfg)
    pricer = PriceEngine(cfg)
    evaluator = Evaluator(cfg, ledger, pricer)

    rpt = evaluator.generate_report()
    console.print(format_evaluation_report(rpt))
    ledger.close()


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show portfolio balance, open positions, recent trades."""
    cfg = _get_config(ctx)

    from polyclaw.ledger import TradeLedger
    from polyclaw.utils.formatting import format_portfolio_status

    # ── Live Polymarket portfolio ──
    _print_live_portfolio(cfg)

    # ── Polyclaw ledger activity ──
    ledger = TradeLedger(cfg)

    snapshot = ledger.get_latest_snapshot(mode=cfg.mode)
    if snapshot:
        console.print(format_portfolio_status(snapshot))

    # Recent trades
    trades = ledger.get_trades(mode=cfg.mode, limit=5)
    if trades:
        console.print("\n[bold]Recent Polyclaw Trades:[/bold]")
        for t in trades:
            pnl_str = f"P&L=${t['pnl']:+.2f}" if t.get("pnl") is not None else "pending"
            console.print(
                f"  {t['timestamp'][:16]}  {t['side']} {t['outcome']} "
                f"@ ${t['fill_price']:.4f}  [{t['strategy']}]  {pnl_str}"
            )
    elif not snapshot:
        console.print(f"[dim]No Polyclaw trades yet (mode: {cfg.mode.upper()}).[/dim]")

    ledger.close()


@main.command(name="config")
@click.pass_context
def show_config(ctx: click.Context) -> None:
    """Show current configuration."""
    import json
    from dataclasses import asdict

    cfg = _get_config(ctx)
    # Convert to dict, excluding sensitive fields
    d = asdict(cfg)
    # Remove private key values
    if "polymarket" in d:
        d["polymarket"].pop("private_key", None)

    console.print_json(json.dumps(d, indent=2, default=str))


@main.command()
@click.option("--query", default=None, help="Search query")
@click.option("--limit", default=10, help="Max results")
@click.pass_context
def search(ctx: click.Context, query: str | None, limit: int) -> None:
    """Search markets by keyword."""
    cfg = _get_config(ctx)

    if not query:
        console.print("[red]Please provide a --query[/red]")
        return

    from polyclaw.fetcher import MarketFetcher
    from polyclaw.utils.formatting import format_markets_table

    fetcher = MarketFetcher(cfg)
    events = fetcher.search_events(query, limit=limit)

    if not events:
        console.print(f"[yellow]No markets found for '{query}'[/yellow]")
        return

    console.print(format_markets_table(events))


# ---------------------------------------------------------------------------
# Trading commands
# ---------------------------------------------------------------------------


@main.command()
@click.argument("token_id")
@click.option("--side", required=True, type=click.Choice(["BUY", "SELL"]), help="Order side")
@click.option("--price", required=True, type=float, help="Limit price (0–1)")
@click.option("--size", required=True, type=float, help="Size in USDC")
@click.option("--outcome", default="Yes", type=click.Choice(["Yes", "No"]), help="Outcome label")
@click.option("--order-type", "order_type", default="GTC", type=click.Choice(["GTC", "FOK", "FAK"]), help="Order type")
@click.option("--market-id", "market_id", default="", help="Condition ID (optional, for ledger)")
@click.option("--neg-risk", "neg_risk", is_flag=True, default=False, help="Enable neg-risk flag")
@click.pass_context
def order(
    ctx: click.Context,
    token_id: str,
    side: str,
    price: float,
    size: float,
    outcome: str,
    order_type: str,
    market_id: str,
    neg_risk: bool,
) -> None:
    """Place a limit or market order on Polymarket."""
    cfg = _get_config(ctx)

    from polyclaw.models import TradeSignal, MarketContext, PolymarketEvent, PolymarketMarket

    signal = TradeSignal(
        market_id=market_id or token_id,
        token_id=token_id,
        side=side,
        outcome=outcome,
        price=price,
        size=size,
        confidence=1.0,
        reasoning="manual CLI order",
        order_type=order_type,
        strategy="cli",
        neg_risk=neg_risk,
    )

    if cfg.mode == "mock":
        from polyclaw.mock_executor import MockExecutor

        executor = MockExecutor(cfg)
        result = executor.execute(signal, MarketContext(
            event=PolymarketEvent(id="", slug="", title=""),
            market=PolymarketMarket(condition_id=market_id, question="", token_id_yes=token_id, token_id_no=""),
        ))
        if result.success:
            console.print(f"[green]Mock order filled[/green] — fill ${result.fill_price:.4f}, balance ${result.balance_after:,.2f}")
        else:
            console.print(f"[red]Mock order failed: {result.error}[/red]")
    else:
        from polyclaw.executor import TradeExecutor

        executor = TradeExecutor(cfg)
        result = executor.execute(signal, MarketContext(
            event=PolymarketEvent(id="", slug="", title=""),
            market=PolymarketMarket(condition_id=market_id, question="", token_id_yes=token_id, token_id_no=""),
        ))
        if result.success:
            console.print(f"[green]Order placed[/green] — order_id={result.order_id}, status={result.status}")
        else:
            console.print(f"[red]Order failed: {result.error}[/red]")


@main.command()
@click.argument("order_id")
@click.pass_context
def cancel(ctx: click.Context, order_id: str) -> None:
    """Cancel a specific open order by ID."""
    cfg = _get_config(ctx)

    from polyclaw.executor import TradeExecutor

    executor = TradeExecutor(cfg)
    ok = executor.cancel_order(order_id)
    if ok:
        console.print(f"[green]Order {order_id} cancelled.[/green]")
    else:
        console.print(f"[red]Failed to cancel order {order_id}.[/red]")


@main.command(name="cancel-all")
@click.pass_context
def cancel_all(ctx: click.Context) -> None:
    """Cancel all open orders."""
    cfg = _get_config(ctx)

    from polyclaw.executor import TradeExecutor

    executor = TradeExecutor(cfg)
    result = executor.cancel_all()
    if result:
        console.print("[green]All open orders cancelled.[/green]")
    else:
        console.print("[red]Failed to cancel orders (or none open).[/red]")


@main.command()
@click.argument("condition_id")
@click.pass_context
def resolve(ctx: click.Context, condition_id: str) -> None:
    """Check if a market has resolved and show outcome."""
    cfg = _get_config(ctx)

    from polyclaw.fetcher import MarketFetcher

    fetcher = MarketFetcher(cfg)
    data = fetcher.get_market_status(condition_id)

    if data is None:
        console.print(f"[red]Market {condition_id} not found.[/red]")
        return

    question = data.get("question", "Unknown")
    closed = data.get("closed", False)
    resolution = data.get("resolution", None)

    console.print(f"[bold]Market:[/bold] {question}")
    console.print(f"  Condition ID: {condition_id}")
    console.print(f"  Closed:       {'Yes' if closed else 'No'}")
    if resolution:
        console.print(f"  Resolution:   [green]{resolution}[/green]")
    else:
        console.print("  Resolution:   [yellow]Unresolved[/yellow]")


@main.command()
@click.pass_context
def balance(ctx: click.Context) -> None:
    """Show Polymarket account balance (cash + positions)."""
    cfg = _get_config(ctx)
    _print_live_portfolio(cfg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print_live_portfolio(cfg) -> None:
    """Fetch and display the live Polymarket portfolio balance."""
    pk = cfg.polymarket.private_key
    funder = cfg.polymarket.funder_address
    if not pk:
        console.print("[yellow]No private key configured — cannot fetch live portfolio.[/yellow]")
        return

    import requests
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import BalanceAllowanceParams

    sig_type = cfg.polymarket.signature_type

    # Fetch cash balance via CLOB
    cash = None
    try:
        client = ClobClient(
            cfg.polymarket.host, key=pk,
            chain_id=cfg.polymarket.chain_id,
            funder=funder or None, signature_type=sig_type,
        )
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        bal = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type="COLLATERAL", signature_type=sig_type)
        )
        if bal and isinstance(bal, dict):
            cash = float(bal.get("balance", "0")) / 1e6
    except Exception as exc:
        logger.debug("Could not fetch CLOB balance: %s", exc)

    # Fetch positions via Data API
    pos_value = None
    open_count = 0
    try:
        if funder:
            resp_v = requests.get(
                "https://data-api.polymarket.com/value",
                params={"user": funder}, timeout=10,
            )
            if resp_v.status_code == 200:
                vdata = resp_v.json()
                if isinstance(vdata, list) and vdata:
                    vdata = vdata[0]
                if isinstance(vdata, dict) and vdata.get("value") is not None:
                    pos_value = float(vdata["value"])

            resp_p = requests.get(
                "https://data-api.polymarket.com/positions",
                params={"user": funder, "sizeThreshold": "0"}, timeout=10,
            )
            if resp_p.status_code == 200:
                all_pos = resp_p.json()
                if isinstance(all_pos, list):
                    open_count = sum(1 for p in all_pos if not p.get("redeemable", False))
    except Exception as exc:
        logger.debug("Could not fetch positions: %s", exc)

    # Display
    console.print("[bold]Polymarket Portfolio[/bold]")
    parts = []
    if cash is not None:
        parts.append(f"Cash: [green]${cash:,.2f}[/green]")
    if pos_value is not None:
        parts.append(f"Positions: [cyan]${pos_value:,.2f}[/cyan] ({open_count} open)")
    if cash is not None and pos_value is not None:
        total = cash + pos_value
        parts.append(f"Total: [bold]${total:,.2f}[/bold]")
    if parts:
        console.print("  " + "  |  ".join(parts))
    else:
        console.print("  [yellow]Could not fetch portfolio data.[/yellow]")
    console.print()


if __name__ == "__main__":
    main()
