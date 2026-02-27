"""Kalshi 15-minute crypto HFT entry point.

Single-pass, user-driven workflow:
1. Fetch balance + open positions
2. Check settled trades → update PnL
3. Fetch upcoming 15-min markets
4. Fetch spot prices
5. Run strategy → recommendations
6. Display recommendations + PnL summary
7. Prompt user to approve trades
8. Execute approved trades

Usage:
    python -m src.hft
    python -m src.hft --asset ETH
    python -m src.hft --assets BTC,ETH,SOL
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from rich.console import Console
from rich.table import Table

from .exchanges.kalshi import KalshiClient
from .core.strategy import find_opportunities, compute_ema, ENABLED_ASSETS
from .core.executor import present_recommendations, prompt_user_approval, execute_trade
from .data.pnl import get_summary, update_settled_trades

console = Console()
logger = logging.getLogger(__name__)


async def run(assets: list[str]):
    """Main HFT scan + trade loop (single pass)."""
    console.print("\n[bold cyan]═══ Kalshi 15-Min Crypto HFT ═══[/bold cyan]\n")

    # ── Connect to Kalshi ──────────────────────────────────────
    client = KalshiClient.from_env()

    async with client:
        # ── 1. Account status ──────────────────────────────────
        console.print("[dim]Fetching account status...[/dim]")
        try:
            balance = await client.get_balance()
        except Exception as e:
            console.print(f"[red]Failed to fetch balance: {e}[/red]")
            balance = 0.0

        try:
            positions = await client.get_positions()
        except Exception as e:
            console.print(f"[yellow]Failed to fetch positions: {e}[/yellow]")
            positions = []

        # ── 2. Update settled trades ───────────────────────────
        settled = update_settled_trades(positions)
        if settled:
            console.print(f"[green]Updated {settled} settled trade(s)[/green]")

        # ── 3. PnL summary ────────────────────────────────────
        summary = get_summary()
        summary.balance = balance

        _print_account_summary(summary, positions)

        if balance <= 0:
            console.print("[yellow bold]⚠  Balance is $0. Fund your Kalshi account to trade.[/yellow bold]")
            console.print("[dim]Continuing to show opportunities anyway...[/dim]\n")

        # ── 4. Fetch markets + spot prices ─────────────────────
        console.print("[dim]Fetching markets and spot prices...[/dim]")

        all_markets = []
        spot_prices = {}

        for asset in assets:
            try:
                markets = await client.get_15min_markets(asset, max_windows=3)
                all_markets.extend(markets)
                console.print(f"  {asset}: {len(markets)} open 15-min markets (3 windows)")
            except Exception as e:
                console.print(f"  [red]{asset} markets failed: {e}[/red]")

        # Batch spot price fetch
        try:
            spot_prices = await client.get_spot_prices(assets)
            for asset, price in spot_prices.items():
                console.print(f"  {asset} spot: ${price:,.2f}")
        except Exception as e:
            console.print(f"  [red]Spot prices failed: {e}[/red]")

        # Fetch 1-min candles for EMA-20
        ema_data = {}
        for asset in assets:
            try:
                candles = await client.get_candles(asset, limit=24)
                ema_data[asset] = compute_ema(candles)
                if ema_data[asset]:
                    console.print(f"  {asset} EMA-20: ${ema_data[asset]:,.2f}")
            except Exception as e:
                console.print(f"  [yellow]{asset} candles failed: {e}[/yellow]")
                ema_data[asset] = None

        if not all_markets:
            console.print("\n[yellow]No open 15-min markets found. Markets may be between sessions.[/yellow]")
            console.print("[dim]Try again when markets are open (15-min windows throughout the day).[/dim]\n")
            return

        if not spot_prices:
            console.print("\n[red]Failed to fetch any spot prices. Cannot run strategy.[/red]\n")
            return

        # ── 5. Run strategy ────────────────────────────────────
        console.print("\n[dim]Running strategy...[/dim]")
        recs = find_opportunities(all_markets, spot_prices, balance, ema_data=ema_data)

        # ── 6. Display recommendations ─────────────────────────
        present_recommendations(recs)

        if not recs:
            console.print("[dim]No mispriced contracts found this cycle. Try again in a few minutes.[/dim]\n")
            return

        # ── 7. Prompt for approval ─────────────────────────────
        console.print("[bold]Review each trade below:[/bold]\n")
        approved = []
        for i, rec in enumerate(recs, 1):
            if prompt_user_approval(rec, i):
                approved.append(rec)
            else:
                console.print("  [dim]Skipped.[/dim]")
            console.print()

        if not approved:
            console.print("[yellow]No trades approved. Exiting.[/yellow]\n")
            return

        # ── 8. Execute approved trades ─────────────────────────
        console.print(f"[bold green]Executing {len(approved)} trade(s)...[/bold green]\n")
        results = []
        for rec in approved:
            result = await execute_trade(client, rec)
            if result:
                results.append(result)

        # ── Final summary ──────────────────────────────────────
        console.print(f"\n[bold]Executed {len(results)}/{len(approved)} trades.[/bold]")
        if results:
            total_cost = sum(r.price * r.count + r.fee for r in results)
            console.print(f"Total cost: ${total_cost:.2f}")
        console.print()


def _print_account_summary(summary, positions):
    """Print account and PnL summary."""
    table = Table(title="Account Summary", show_lines=False, padding=(0, 2))
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Balance", f"${summary.balance:.2f}")
    table.add_row("Open Positions", str(len(positions)))
    table.add_row("Total Trades", str(summary.total_trades))
    table.add_row("Realized PnL", _pnl_color(summary.realized_pnl))
    table.add_row("Wins / Losses", f"{summary.wins} / {summary.losses}")
    table.add_row("Win Rate", f"{summary.win_rate:.1f}%")

    console.print()
    console.print(table)
    console.print()


def _pnl_color(pnl: float) -> str:
    if pnl > 0:
        return f"[green]+${pnl:.4f}[/green]"
    elif pnl < 0:
        return f"[red]-${abs(pnl):.4f}[/red]"
    return "$0.00"


def main():
    parser = argparse.ArgumentParser(description="Kalshi 15-min crypto HFT")
    parser.add_argument(
        "--assets",
        default=",".join(ENABLED_ASSETS),
        help=f"Comma-separated assets to scan (default: {','.join(ENABLED_ASSETS)})",
    )
    parser.add_argument(
        "--asset",
        default=None,
        help="Single asset to scan (shorthand for --assets)",
    )
    args = parser.parse_args()

    if args.asset:
        assets = [args.asset.upper()]
    else:
        assets = [a.strip().upper() for a in args.assets.split(",")]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    asyncio.run(run(assets))


if __name__ == "__main__":
    main()
