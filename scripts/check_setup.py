#!/usr/bin/env python3
"""Verify Polymarket credentials and connectivity.

Run:
    python scripts/check_setup.py

Checks performed:
    1. .env file exists and loads
    2. POLYCLAW_PRIVATE_KEY is set and looks valid
    3. POLYCLAW_FUNDER_ADDRESS is set and looks valid
    4. Polygon RPC is reachable (chain_id 137)
    5. CLOB API is reachable (public endpoint)
    6. py-clob-client can initialise with the key
    7. Derive API key from CLOB (proves key is accepted)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ── ensure project root is importable ──────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"
WARN = "\033[93m⚠ WARN\033[0m"


def _status(ok: bool) -> str:
    return PASS if ok else FAIL


def main() -> None:
    print("=" * 60)
    print("  Polyclaw — Polymarket Setup Check")
    print("=" * 60)
    errors: list[str] = []

    # ── 1. .env file ──────────────────────────────────────────────
    env_path = ROOT / ".env"
    env_exists = env_path.exists()
    print(f"\n[1] .env file exists              {_status(env_exists)}")
    if env_exists:
        load_dotenv(env_path, override=True)
        print(f"    Loaded from: {env_path}")
    else:
        print(f"    {WARN} No .env file at {env_path}")
        print("    Will check environment variables directly.")
        load_dotenv()  # try system env

    # ── 2. Private key ────────────────────────────────────────────
    pk = os.environ.get("POLYCLAW_PRIVATE_KEY", "").strip()
    pk_set = bool(pk)
    pk_looks_valid = pk_set and pk.startswith("0x") and len(pk) == 66
    print(f"\n[2] POLYCLAW_PRIVATE_KEY is set    {_status(pk_set)}")
    if pk_set:
        masked = pk[:6] + "…" + pk[-4:]
        print(f"    Value: {masked}")
        print(f"    Looks like a valid hex key    {_status(pk_looks_valid)}")
        if not pk_looks_valid:
            if not pk.startswith("0x"):
                print(f"    {WARN} Should start with '0x'")
            if len(pk) != 66:
                print(f"    {WARN} Expected 66 chars (0x + 64 hex digits), got {len(pk)}")
    else:
        errors.append("POLYCLAW_PRIVATE_KEY not set")

    # ── 3. Funder address ─────────────────────────────────────────
    funder = os.environ.get("POLYCLAW_FUNDER_ADDRESS", "").strip()
    funder_set = bool(funder)
    funder_looks_valid = funder_set and funder.startswith("0x") and len(funder) == 42
    print(f"\n[3] POLYCLAW_FUNDER_ADDRESS is set {_status(funder_set)}")
    if funder_set:
        masked_f = funder[:6] + "…" + funder[-4:]
        print(f"    Value: {masked_f}")
        print(f"    Looks like a valid address    {_status(funder_looks_valid)}")
        if not funder_looks_valid:
            if not funder.startswith("0x"):
                print(f"    {WARN} Should start with '0x'")
            if len(funder) != 42:
                print(f"    {WARN} Expected 42 chars (0x + 40 hex digits), got {len(funder)}")
    else:
        errors.append("POLYCLAW_FUNDER_ADDRESS not set")

    # ── 4. CLOB API reachable ─────────────────────────────────────
    print(f"\n[4] CLOB API reachable ({CLOB_HOST})")
    try:
        import requests

        resp = requests.get(f"{CLOB_HOST}/time", timeout=10)
        api_ok = resp.status_code == 200
        print(f"    GET /time status={resp.status_code}  {_status(api_ok)}")
        if api_ok:
            print(f"    Server time: {resp.text.strip()[:60]}")
    except Exception as exc:
        api_ok = False
        print(f"    {FAIL}  {exc}")
        errors.append(f"CLOB API unreachable: {exc}")

    # ── 5. Fetch a market (public, no auth) ───────────────────────
    print(f"\n[5] Fetch a public market (Gamma API)")
    try:
        import requests

        resp = requests.get(
            "https://gamma-api.polymarket.com/events",
            params={"limit": 1, "active": True, "closed": False},
            timeout=10,
        )
        gamma_ok = resp.status_code == 200 and len(resp.json()) > 0
        print(f"    GET /events status={resp.status_code}  {_status(gamma_ok)}")
        if gamma_ok:
            ev = resp.json()[0]
            print(f"    Sample event: {ev.get('title', '?')[:60]}")
    except Exception as exc:
        gamma_ok = False
        print(f"    {FAIL}  {exc}")

    # ── 6. py-clob-client initialisation ──────────────────────────
    print(f"\n[6] py-clob-client SDK initialisation")
    clob_client = None
    # Use POLY_PROXY (1) when a funder/proxy wallet is set, EOA (0) otherwise
    sig_type = 1 if funder_set else 0
    try:
        from py_clob_client.client import ClobClient

        if pk_set:
            clob_client = ClobClient(
                CLOB_HOST,
                key=pk,
                chain_id=CHAIN_ID,
                funder=funder or None,
                signature_type=sig_type,
            )
            sig_label = "POLY_PROXY" if sig_type == 1 else "EOA"
            print(f"    ClobClient created (authenticated, {sig_label})  {PASS}")
        else:
            clob_client = ClobClient(CLOB_HOST, chain_id=CHAIN_ID)
            print(f"    ClobClient created (read-only)  {PASS}")
            print(f"    {WARN} No private key — trading will not work")
    except Exception as exc:
        print(f"    {FAIL}  {exc}")
        errors.append(f"ClobClient init failed: {exc}")

    # ── 7. Derive API key (proves key + funder are accepted) ──────
    print(f"\n[7] Derive API key (auth handshake)")
    api_creds = None
    if clob_client and pk_set:
        try:
            creds = clob_client.derive_api_key()
            if creds and hasattr(creds, "api_key") and creds.api_key:
                print(f"    API key derived successfully  {PASS}")
                masked_api = creds.api_key[:8] + "…"
                print(f"    API key: {masked_api}")
                api_creds = creds
            elif isinstance(creds, dict) and creds.get("apiKey"):
                print(f"    API key derived successfully  {PASS}")
                masked_api = creds["apiKey"][:8] + "…"
                print(f"    API key: {masked_api}")
                api_creds = creds
            else:
                print(f"    {FAIL}  Unexpected response: {str(creds)[:100]}")
                errors.append("derive_api_key returned unexpected format")
        except Exception as exc:
            print(f"    {FAIL}  {exc}")
            errors.append(f"derive_api_key failed: {exc}")

        # Re-create the client with API credentials so authenticated
        # endpoints (like balance) work in subsequent checks.
        if api_creds:
            try:
                from py_clob_client.client import ClobClient
                from py_clob_client.clob_types import ApiCreds as ApiCredsType

                # If api_creds is already an ApiCreds object, use it directly.
                # If it's a dict, wrap it into an ApiCreds object.
                if isinstance(api_creds, ApiCredsType):
                    creds_obj = api_creds
                elif isinstance(api_creds, dict):
                    creds_obj = ApiCredsType(
                        api_key=api_creds.get("apiKey", api_creds.get("api_key", "")),
                        api_secret=api_creds.get("secret", api_creds.get("api_secret", "")),
                        api_passphrase=api_creds.get("passphrase", api_creds.get("api_passphrase", "")),
                    )
                else:
                    creds_obj = None

                if creds_obj:
                    clob_client = ClobClient(
                        CLOB_HOST,
                        key=pk,
                        chain_id=CHAIN_ID,
                        funder=funder or None,
                        signature_type=sig_type,
                        creds=creds_obj,
                    )
                print(f"    Client re-initialised with API creds  {PASS}")
            except Exception as exc:
                print(f"    {WARN} Could not apply API creds: {exc}")
    elif not pk_set:
        print(f"    {WARN} Skipped — no private key")
    else:
        print(f"    {WARN} Skipped — ClobClient not available")

    # ── 8. On-chain balances (MATIC + USDC on Polygon) ────────────
    print(f"\n[8] Funder wallet on-chain balances (Polygon PoS)")
    print(f"    Note: Polyclaw trades via the CLOB API, not on-chain.")
    print(f"    Your trading funds live inside Polymarket (see check 9).")
    print(f"    On-chain balances here are only needed if you want to")
    print(f"    deposit more funds or interact with contracts directly.")
    if funder_set:
        try:
            import requests

            POLYGON_RPC = "https://polygon-rpc.com"
            # USDC.e on Polygon (bridged USDC used by Polymarket)
            USDC_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
            # USDC native on Polygon
            USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

            addr_no_prefix = funder.lower().replace("0x", "")

            # 8a. MATIC balance (native gas token)
            payload_matic = {
                "jsonrpc": "2.0", "id": 1, "method": "eth_getBalance",
                "params": [funder, "latest"],
            }
            resp_m = requests.post(POLYGON_RPC, json=payload_matic, timeout=10)
            matic_wei = int(resp_m.json().get("result", "0x0"), 16)
            matic_bal = matic_wei / 1e18

            # 8b. USDC.e balance (ERC-20 balanceOf)
            data_usdc_e = f"0x70a08231000000000000000000000000{addr_no_prefix}"
            payload_usdc_e = {
                "jsonrpc": "2.0", "id": 2, "method": "eth_call",
                "params": [{"to": USDC_CONTRACT, "data": data_usdc_e}, "latest"],
            }
            resp_ue = requests.post(POLYGON_RPC, json=payload_usdc_e, timeout=10)
            usdc_e_raw = int(resp_ue.json().get("result", "0x0"), 16)
            usdc_e_bal = usdc_e_raw / 1e6  # USDC has 6 decimals

            # 8c. USDC (native) balance
            payload_usdc_n = {
                "jsonrpc": "2.0", "id": 3, "method": "eth_call",
                "params": [{"to": USDC_NATIVE, "data": data_usdc_e}, "latest"],
            }
            resp_un = requests.post(POLYGON_RPC, json=payload_usdc_n, timeout=10)
            usdc_n_raw = int(resp_un.json().get("result", "0x0"), 16)
            usdc_n_bal = usdc_n_raw / 1e6

            print(f"    POL (gas):      {matic_bal:,.6f} POL")
            print(f"    USDC.e:         ${usdc_e_bal:,.2f}")
            print(f"    USDC (native):  ${usdc_n_bal:,.2f}")
            print(f"    {PASS}")

        except Exception as exc:
            print(f"    {FAIL}  Could not fetch balances: {exc}")
    else:
        print(f"    {WARN} Skipped — no funder address")

    # ── 9. Polymarket portfolio balance ─────────────────────────
    print(f"\n[9] Polymarket portfolio")

    cash_balance = None
    positions_value = None
    open_positions = []

    # 9a. Available cash via authenticated CLOB client
    #     signature_type=1 (POLY_PROXY) queries the proxy wallet balance.
    if clob_client and pk_set and api_creds:
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams

            bal = clob_client.get_balance_allowance(
                BalanceAllowanceParams(
                    asset_type="COLLATERAL",
                    signature_type=sig_type,
                )
            )
            if bal and isinstance(bal, dict):
                raw = float(bal.get("balance", "0"))
                # Balance is in USDC atomic units (6 decimals)
                cash_balance = raw / 1e6
        except Exception as exc:
            print(f"    Cash balance:       {WARN} {exc}")

    # 9b. Positions via Data API (filter to open only)
    if funder_set:
        try:
            import requests

            resp_p = requests.get(
                "https://data-api.polymarket.com/positions",
                params={"user": funder, "sizeThreshold": "0"},
                timeout=10,
            )
            if resp_p.status_code == 200:
                all_positions = resp_p.json()
                if isinstance(all_positions, list):
                    # redeemable=True means settled/resolved; exclude those
                    open_positions = [
                        p for p in all_positions
                        if not p.get("redeemable", False)
                    ]
                    settled = [
                        p for p in all_positions
                        if p.get("redeemable", False)
                    ]
        except Exception:
            pass

        # Positions value from /value endpoint (covers open positions)
        try:
            import requests

            resp_v = requests.get(
                "https://data-api.polymarket.com/value",
                params={"user": funder},
                timeout=10,
            )
            if resp_v.status_code == 200:
                vdata = resp_v.json()
                if isinstance(vdata, list) and vdata:
                    vdata = vdata[0]
                if isinstance(vdata, dict) and vdata.get("value") is not None:
                    positions_value = float(vdata["value"])
        except Exception:
            pass

    # ── Display ──
    if cash_balance is not None:
        print(f"    Available to trade: ${cash_balance:,.2f}")
    if positions_value is not None:
        print(f"    Positions value:   ${positions_value:,.2f}")
    if cash_balance is not None and positions_value is not None:
        portfolio_total = cash_balance + positions_value
        print(f"    Portfolio total:    ${portfolio_total:,.2f}")

    if open_positions:
        total_cost = sum(float(p.get("initialValue", 0) or 0) for p in open_positions)
        total_pnl = sum(float(p.get("cashPnl", 0) or 0) for p in open_positions)
        print(f"    ─────────────────────────────")
        print(f"    Open positions ({len(open_positions)}):")
        for pos in open_positions:
            title = pos.get("title", "?")[:45]
            outcome = pos.get("outcome", "?")
            size = float(pos.get("size", 0) or 0)
            avg = float(pos.get("avgPrice", 0) or 0)
            cur = float(pos.get("curPrice", 0) or 0)
            cur_val = float(pos.get("currentValue", 0) or 0)
            pnl_pct = float(pos.get("percentPnl", 0) or 0)
            print(f"      {outcome:3s} {size:.1f} shares  {avg*100:.0f}¢→{cur*100:.0f}¢  ${cur_val:.2f}  ({pnl_pct:+.1f}%)  {title}")
        print(f"    Cost basis: ${total_cost:,.2f}  P&L: ${total_pnl:+,.2f}")

    if settled:
        n_redeemable = len(settled)
        print(f"    Settled positions: {n_redeemable} (redeemable)")

    has_data = cash_balance is not None or positions_value is not None or open_positions
    if has_data:
        print(f"    {PASS}")
    else:
        if not pk_set and not funder_set:
            print(f"    {WARN} Skipped — no credentials set")
        else:
            print(f"    {WARN} Could not retrieve portfolio data.")
            print(f"    Trading via py-clob-client should still work if step 7 passed.")

    # ── Summary ───────────────────────────────────────────────────
    print("\n" + "=" * 60)
    if not errors:
        print(f"  {PASS}  All checks passed! Your setup is ready.")
    else:
        print(f"  {FAIL}  {len(errors)} issue(s) found:\n")
        for e in errors:
            print(f"    • {e}")
    print("=" * 60)

    sys.exit(0 if not errors else 1)


if __name__ == "__main__":
    main()
