"""Trade executor with user-approval workflow.

Presents recommendations via Rich console and prompts for approval.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

from ..exchanges.kalshi import KalshiClient
from ..models import OrderStatus, Side, TradeRecommendation, TradeRecord
from ..data.pnl import log_trade

logger = logging.getLogger(__name__)
console = Console()


def present_recommendations(recs: list[TradeRecommendation]):
    """Display trade recommendations in a Rich table."""
    if not recs:
        console.print("\n[yellow]No trade opportunities found.[/yellow]\n")
        return

    table = Table(title="Trade Recommendations", show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Ticker", style="cyan")
    table.add_column("Side", style="bold")
    table.add_column("Price", justify="right")
    table.add_column("Fair Value", justify="right")
    table.add_column("Edge", justify="right", style="green")
    table.add_column("Contracts", justify="right")
    table.add_column("Time Left", justify="right")
    table.add_column("Reason")

    for i, rec in enumerate(recs, 1):
        side_style = "green" if rec.side == Side.YES else "red"
        table.add_row(
            str(i),
            rec.ticker,
            f"[{side_style}]{rec.side.value.upper()}[/{side_style}]",
            f"${rec.price:.2f}",
            f"${rec.fair_value:.2f}",
            f"+${rec.edge:.2f}",
            str(rec.count),
            f"{rec.minutes_left:.0f}min",
            rec.reason[:60],
        )

    console.print()
    console.print(table)
    console.print()


def prompt_user_approval(rec: TradeRecommendation, index: int) -> bool:
    """Ask user to approve a specific trade. Returns True if approved."""
    console.print(f"[bold]Trade #{index}:[/bold] {rec.side.value.upper()} {rec.count}x {rec.ticker} @ ${rec.price:.2f}")
    console.print(f"  Fair value: ${rec.fair_value:.2f} | Edge: +${rec.edge:.2f} | {rec.minutes_left:.0f}min left")
    response = console.input("[bold yellow]  Execute? (y/N): [/bold yellow]").strip().lower()
    return response in ("y", "yes")


async def execute_trade(client: KalshiClient, rec: TradeRecommendation) -> TradeRecord | None:
    """Place an order on Kalshi and log it.

    Returns the TradeRecord on success, None on failure.
    """
    price_cents = int(round(rec.price * 100))
    price_cents = max(1, min(99, price_cents))

    try:
        order = await client.create_order(
            ticker=rec.ticker,
            side=rec.side.value,
            price_cents=price_cents,
            count=rec.count,
            order_type="limit",
            action="buy",
        )

        order_id = order.get("order_id", order.get("id", "unknown"))
        status_str = order.get("status", "pending").lower()
        status_map = {
            "resting": OrderStatus.PENDING,
            "pending": OrderStatus.PENDING,
            "executed": OrderStatus.FILLED,
            "filled": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "cancelled": OrderStatus.CANCELLED,
        }

        fee = KalshiClient.estimate_fee(rec.price, rec.count)

        record = TradeRecord(
            id=order_id,
            ticker=rec.ticker,
            side=rec.side,
            price=rec.price,
            count=rec.count,
            fee=fee,
            timestamp=datetime.now(timezone.utc).isoformat(),
            status=status_map.get(status_str, OrderStatus.PENDING),
        )

        log_trade(record)
        console.print(f"  [green]Order placed: {order_id} ({status_str})[/green]")
        return record

    except Exception as e:
        console.print(f"  [red]Order failed: {e}[/red]")
        logger.error(f"Order failed for {rec.ticker}: {e}")
        return None
