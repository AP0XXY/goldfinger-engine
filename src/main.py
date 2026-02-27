"""Prediction market arbitrage scanner.

Scans Kalshi and Polymarket for matching crypto binary markets,
detects cross-platform arbitrage opportunities, and logs spread data.

Usage:
    python -m src.main              # Single scan
    python -m src.main --loop       # Continuous scanning
    python -m src.main --loop --interval 10
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel

from .exchanges.kalshi import KalshiClient
from .exchanges.polymarket import PolymarketClient
from .core.matcher import match_markets
from .core.arbitrage import scan_all_opportunities
from .data.collector import SpreadLogger, ScanResultLogger
from .data.database import init_db, save_signal

console = Console()
logger = logging.getLogger("arb")


async def run_scan(
    kalshi: KalshiClient,
    poly: PolymarketClient,
    spread_logger: SpreadLogger,
    scan_logger: ScanResultLogger,
    min_spread: float = 0.02,
    min_spread_pct: float = 1.5,
) -> None:
    """Run a single arbitrage scan across platforms."""
    console.print(f"\n[bold cyan]{'='*60}")
    console.print(f"[bold cyan]  Arbitrage Scan @ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    console.print(f"[bold cyan]{'='*60}\n")

    # Fetch markets concurrently
    console.print("[dim]Fetching markets from Kalshi and Polymarket...[/dim]")
    kalshi_markets, poly_markets = await asyncio.gather(
        kalshi.get_crypto_markets(),
        poly.get_crypto_markets(),
    )

    console.print(f"  Kalshi:      [green]{len(kalshi_markets)}[/green] crypto markets")
    console.print(f"  Polymarket:  [green]{len(poly_markets)}[/green] crypto markets")

    if not kalshi_markets or not poly_markets:
        console.print("[yellow]Not enough markets to scan. Skipping.[/yellow]")
        return

    # Match markets across platforms
    console.print("\n[dim]Matching markets across platforms...[/dim]")
    matched = match_markets(kalshi_markets, poly_markets)
    console.print(f"  Matched:     [green]{len(matched)}[/green] market pairs")

    # Log all matched spreads
    for m in matched:
        spread_logger.log_matched_spread(m)

    # Detect arbitrage
    console.print(f"\n[dim]Scanning for arbitrage (min spread: ${min_spread}, min %: {min_spread_pct}%)...[/dim]")
    opportunities = scan_all_opportunities(matched, min_spread, min_spread_pct)

    # Save opportunities to database
    for opp in opportunities:
        save_signal({
            "timestamp": opp.timestamp,
            "event_description": opp.matched_market.event_description,
            "buy_yes_platform": opp.buy_yes_platform.value,
            "buy_yes_price": opp.buy_yes_price,
            "buy_no_platform": opp.buy_no_platform.value,
            "buy_no_price": opp.buy_no_price,
            "gross_spread": opp.gross_spread,
            "estimated_fees": opp.estimated_fees,
            "net_spread": opp.net_spread,
            "net_spread_pct": opp.net_spread_pct,
            "cost": opp.cost,
            "is_profitable": int(opp.is_profitable),
        })

    # Log scan results
    scan_logger.log_scan(
        matched_count=len(matched),
        opportunities=opportunities,
        kalshi_count=len(kalshi_markets),
        poly_count=len(poly_markets),
    )

    # Display results
    if opportunities:
        console.print(f"\n[bold green]Found {len(opportunities)} arbitrage opportunities![/bold green]\n")
        _display_opportunities(opportunities)
    else:
        console.print("\n[yellow]No arbitrage opportunities found this scan.[/yellow]")
        if matched:
            console.print("[dim]Showing top matched market spreads:[/dim]\n")
            _display_matched_spreads(matched[:10])


def _display_opportunities(opportunities):
    table = Table(title="Arbitrage Opportunities", show_lines=True)
    table.add_column("Event", style="white", max_width=40)
    table.add_column("Buy YES", style="green")
    table.add_column("Buy NO", style="green")
    table.add_column("Cost", style="cyan")
    table.add_column("Gross", style="yellow")
    table.add_column("Fees", style="red")
    table.add_column("Net", style="bold green")
    table.add_column("Net %", style="bold green")

    for opp in opportunities:
        table.add_row(
            opp.matched_market.event_description,
            f"{opp.buy_yes_platform.value}\n${opp.buy_yes_price:.4f}",
            f"{opp.buy_no_platform.value}\n${opp.buy_no_price:.4f}",
            f"${opp.cost:.4f}",
            f"${opp.gross_spread:.4f}",
            f"${opp.estimated_fees:.4f}",
            f"${opp.net_spread:.4f}",
            f"{opp.net_spread_pct:.2f}%",
        )

    console.print(table)


def _display_matched_spreads(matched):
    table = Table(title="Matched Market Spreads (top 10)", show_lines=True)
    table.add_column("Event", style="white", max_width=40)
    table.add_column("Platform A", style="cyan")
    table.add_column("YES Price", style="green")
    table.add_column("Platform B", style="cyan")
    table.add_column("YES Price", style="green")
    table.add_column("Spread", style="yellow")
    table.add_column("Confidence", style="dim")

    for m in matched:
        a_yes = m.market_a.yes_price
        b_yes = m.market_b.yes_price
        if a_yes is not None and b_yes is not None:
            diff = abs(a_yes - b_yes)
        else:
            diff = None

        table.add_row(
            m.event_description,
            m.market_a.platform.value,
            f"${a_yes:.4f}" if a_yes else "N/A",
            m.market_b.platform.value,
            f"${b_yes:.4f}" if b_yes else "N/A",
            f"${diff:.4f}" if diff else "N/A",
            f"{m.match_confidence:.0%}",
        )

    console.print(table)


async def main_async(args: argparse.Namespace):
    # Initialize database
    init_db()
    
    spread_logger = SpreadLogger()
    scan_logger = ScanResultLogger()

    # Use production Kalshi API for real data
    kalshi_url = "https://api.elections.kalshi.com/trade-api/v2"
    async with KalshiClient(base_url=kalshi_url) as kalshi, PolymarketClient() as poly:
        if args.loop:
            console.print(Panel(
                f"[bold]Continuous scanning mode[/bold]\n"
                f"Interval: {args.interval}s | Min spread: ${args.min_spread} | Min %: {args.min_pct}%\n"
                f"Press Ctrl+C to stop",
                title="Arbitrage Scanner",
                border_style="cyan",
            ))
            scan_num = 0
            while True:
                scan_num += 1
                console.print(f"\n[dim]--- Scan #{scan_num} ---[/dim]")
                try:
                    await run_scan(
                        kalshi, poly, spread_logger, scan_logger,
                        min_spread=args.min_spread,
                        min_spread_pct=args.min_pct,
                    )
                except Exception as e:
                    console.print(f"[red]Scan error: {e}[/red]")
                    logger.exception("Scan failed")
                await asyncio.sleep(args.interval)
        else:
            await run_scan(
                kalshi, poly, spread_logger, scan_logger,
                min_spread=args.min_spread,
                min_spread_pct=args.min_pct,
            )


def main():
    parser = argparse.ArgumentParser(description="Prediction Market Arbitrage Scanner")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=30, help="Scan interval in seconds (default: 30)")
    parser.add_argument("--min-spread", type=float, default=0.02, help="Minimum net spread in dollars (default: 0.02)")
    parser.add_argument("--min-pct", type=float, default=1.5, help="Minimum net spread percentage (default: 1.5)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        console.print("\n[yellow]Scanner stopped.[/yellow]")


if __name__ == "__main__":
    main()
