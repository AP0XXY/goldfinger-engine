"""PnL tracking and trade history persistence."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..models import AccountSnapshot, OrderStatus, TradeRecord

logger = logging.getLogger(__name__)

PNL_DIR = Path("data/pnl")
TRADES_FILE = PNL_DIR / "trades.json"


def _ensure_dir():
    PNL_DIR.mkdir(parents=True, exist_ok=True)


def load_trades() -> list[TradeRecord]:
    """Load all trades from disk."""
    if not TRADES_FILE.exists():
        return []
    try:
        with open(TRADES_FILE) as f:
            data = json.load(f)
        return [TradeRecord.from_dict(d) for d in data]
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Failed to load trades: {e}")
        return []


def save_trades(trades: list[TradeRecord]):
    """Save all trades to disk."""
    _ensure_dir()
    with open(TRADES_FILE, "w") as f:
        json.dump([t.to_dict() for t in trades], f, indent=2)


def log_trade(record: TradeRecord):
    """Append a new trade to history."""
    trades = load_trades()
    trades.append(record)
    save_trades(trades)
    logger.info(f"Logged trade: {record.side.value} {record.count}x {record.ticker} @ {record.price}")


def get_summary() -> AccountSnapshot:
    """Compute account summary from trade history."""
    trades = load_trades()

    realized_pnl = 0.0
    unrealized_pnl = 0.0
    wins = 0
    losses = 0
    open_count = 0

    for t in trades:
        if t.status == OrderStatus.FILLED and t.pnl is not None:
            realized_pnl += t.pnl
            if t.pnl > 0:
                wins += 1
            elif t.pnl < 0:
                losses += 1
        elif t.status in (OrderStatus.PENDING, OrderStatus.PARTIAL):
            open_count += 1
            # Unrealized: estimate based on fair value vs entry (rough)
            cost = t.price * t.count + t.fee
            unrealized_pnl -= cost  # worst case: all lost

    return AccountSnapshot(
        balance=0.0,  # caller should set from exchange
        open_positions=open_count,
        realized_pnl=round(realized_pnl, 4),
        unrealized_pnl=round(unrealized_pnl, 4),
        total_trades=len(trades),
        wins=wins,
        losses=losses,
    )


def sync_orders_from_exchange(orders: list[dict]) -> int:
    """Sync order history from Kalshi into local trade log.

    Any orders on Kalshi that we don't have locally get added.
    Returns number of new trades added.
    """
    trades = load_trades()
    existing_ids = {t.id for t in trades}
    added = 0

    for o in orders:
        order_id = o.get("order_id", o.get("id", ""))
        if order_id in existing_ids or not order_id:
            continue
        status_str = o.get("status", "pending").lower()
        if status_str not in ("executed", "filled"):
            continue

        side_str = o.get("side", "yes")
        # Price in cents → dollars
        yes_price = o.get("yes_price", 0)
        price_dollars = float(yes_price) / 100.0 if yes_price else 0.0
        count = o.get("fill_count", o.get("initial_count", 1))

        from ..exchanges.kalshi import KalshiClient
        fee = KalshiClient.estimate_fee(price_dollars, count)

        record = TradeRecord(
            id=order_id,
            ticker=o.get("ticker", ""),
            side=Side(side_str),
            price=price_dollars,
            count=count,
            fee=fee,
            timestamp=o.get("created_time", ""),
            status=OrderStatus.FILLED,
        )
        trades.append(record)
        existing_ids.add(order_id)
        added += 1
        logger.info(f"Synced order from Kalshi: {record.side.value} {record.count}x {record.ticker} @ ${record.price:.2f}")

    if added:
        save_trades(trades)

    return added


def update_settled_trades(market_results: dict[str, str]) -> int:
    """Check if any open trades have settled and update PnL.

    Args:
        market_results: Dict of ticker -> result ("yes" or "no") for settled markets.

    Returns:
        Number of trades updated.
    """
    trades = load_trades()

    updated = 0
    for trade in trades:
        if trade.pnl is not None:
            continue
        result = market_results.get(trade.ticker)
        if result is None:
            continue

        cost = trade.price * trade.count + trade.fee
        won = (trade.side.value == result)  # YES side won if result="yes", etc.

        if won:
            payout = trade.count * 1.0
            trade.settled_price = 1.0
        else:
            payout = 0.0
            trade.settled_price = 0.0

        trade.pnl = round(payout - cost, 4)
        trade.status = OrderStatus.FILLED
        updated += 1
        outcome = "WIN" if won else "LOSS"
        logger.info(f"Trade settled [{outcome}]: {trade.ticker} -> PnL ${trade.pnl:+.4f}")

    if updated:
        save_trades(trades)

    return updated
