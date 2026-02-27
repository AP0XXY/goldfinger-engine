"""Scan orchestration — wraps strategy engine for the web layer.

Calls the exchange client, fetches markets/prices/candles,
runs the strategy, and returns RAW recommendations + stats.

This module does NOT sanitize output — that's firewall.py's job.
All functions are async-native for use inside FastAPI's event loop.

Supports two modes:
  - Standalone: run_scan() / run_trade() → reads from .env
  - Multi-user: run_scan_for_user() / run_trade_for_user() → decrypts creds per-request
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from ..exchanges.kalshi import KalshiClient
from ..core.strategy import find_opportunities, compute_ema, ENABLED_ASSETS
from ..crypto import decrypt
from ..data.pnl import (
    sync_orders_from_exchange,
    update_settled_trades,
    load_trades,
    log_trade,
)
from ..models import OrderStatus, Side, TradeRecommendation, TradeRecord

logger = logging.getLogger(__name__)

PNL_DIR = Path("data/pnl")


# ── Multi-user trade sync (checks Kalshi for settlements) ────

async def sync_trades_for_user(
    api_key_enc: str,
    private_key_enc: str,
    trades: list[dict],
) -> list[dict]:
    """Check Kalshi for settlement results on a list of trades.

    Args:
        api_key_enc: Fernet-encrypted API key.
        private_key_enc: Fernet-encrypted PEM key.
        trades: List of dicts with: id, order_id, ticker, side, price, count, fee

    Returns:
        List of dicts with updated status/pnl for each trade that changed.
    """
    api_key = decrypt(api_key_enc)
    pem_content = decrypt(private_key_enc)
    client = KalshiClient.from_credentials(api_key, pem_content)

    updates = []

    async with client:
        for trade in trades:
            ticker = trade.get("ticker", "")
            trade_id = trade.get("id", "")
            order_id = trade.get("order_id")
            side = trade.get("side", "yes")
            price = float(trade.get("price", 0))
            count = int(trade.get("count", 0))
            fee = float(trade.get("fee", 0))

            try:
                # Check if market has settled
                mdata = await client._get(f"/markets/{ticker}")
                market = mdata.get("market", mdata)
                result = market.get("result", "")
                market_status = market.get("status", "")

                if result in ("yes", "no"):
                    # Market settled — calculate PnL
                    cost = price * count + fee
                    won = (side == result)
                    payout = (count * 1.0) if won else 0.0
                    pnl = round(payout - cost, 4)

                    updates.append({
                        "id": trade_id,
                        "status": "settled",
                        "pnl": pnl,
                        "settled_price": 1.0 if won else 0.0,
                    })
                    outcome = "WIN" if won else "LOSS"
                    logger.info(f"Trade settled [{outcome}]: {ticker} -> PnL ${pnl:+.4f}")

                elif market_status == "closed" and result == "":
                    # Market closed but not yet settled (in settlement process)
                    updates.append({
                        "id": trade_id,
                        "status": "settling",
                    })

                else:
                    # Check order status on Kalshi
                    if order_id:
                        try:
                            odata = await client._get(f"/portfolio/orders/{order_id}", auth=True)
                            order = odata.get("order", odata)
                            new_status = order.get("status", "").lower()
                            if new_status and new_status != trade.get("current_status", ""):
                                updates.append({
                                    "id": trade_id,
                                    "status": new_status,
                                })
                        except Exception as e:
                            logger.debug(f"Order lookup failed for {order_id}: {e}")

            except Exception as e:
                logger.warning(f"Sync failed for {ticker}: {e}")

    return updates


# ── Core scan logic (shared between modes) ───────────────────

async def _scan_with_client(
    client: KalshiClient,
    assets: list[str],
    settle: bool = True,
    use_local_pnl: bool = True,
) -> dict:
    """Run the HFT scanner with a given client instance.

    Args:
        client: An authenticated KalshiClient.
        assets: Which assets to scan.
        settle: If True, sync orders and check settlements.
        use_local_pnl: If True, use local JSON pnl (standalone).
                       If False, skip local pnl (multi-user uses DB).
    """
    async with client:
        balance = await client.get_balance()
        positions = await client.get_positions()

        # Settlement (only standalone mode uses local PnL files)
        if settle and use_local_pnl:
            PNL_DIR.mkdir(parents=True, exist_ok=True)
            try:
                orders = await client.get_open_orders()
                synced = sync_orders_from_exchange(orders)
                if synced:
                    logger.info(f"Synced {synced} orders from Kalshi")
            except Exception as e:
                logger.warning(f"Order sync failed: {e}")

            try:
                trades = load_trades()
                unsettled_tickers = {
                    t.ticker for t in trades if t.pnl is None
                }
                market_results = {}
                for ticker in unsettled_tickers:
                    try:
                        mdata = await client._get(f"/markets/{ticker}")
                        market = mdata.get("market", mdata)
                        result = market.get("result", "")
                        if result in ("yes", "no"):
                            market_results[ticker] = result
                    except Exception:
                        pass
                if market_results:
                    settled_count = update_settled_trades(market_results)
                    if settled_count:
                        logger.info(f"Updated {settled_count} settled trades")
            except Exception as e:
                logger.warning(f"Settlement check failed: {e}")

        # Fetch markets (4 expiry windows = ~60 min of upcoming markets)
        all_markets = []
        for asset in assets:
            try:
                markets = await client.get_15min_markets(asset, max_windows=4)
                all_markets.extend(markets)
            except Exception as e:
                logger.warning(f"{asset} markets failed: {e}")

        # Spot prices
        spot_prices = {}
        try:
            spot_prices = await client.get_spot_prices(assets)
        except Exception as e:
            logger.warning(f"Spot prices failed: {e}")

        # 1-min candles for EMA-20
        ema_data = {}
        for asset in assets:
            try:
                candles = await client.get_candles(asset, limit=24)
                ema_data[asset] = compute_ema(candles)
            except Exception as e:
                logger.warning(f"Candles for {asset} failed: {e}")
                ema_data[asset] = None

        # Run strategy
        recs: list[TradeRecommendation] = []
        if all_markets and spot_prices:
            recs = find_opportunities(
                all_markets, spot_prices, balance, ema_data=ema_data
            )

        # PnL stats (standalone uses local files, multi-user returns zeros — Next.js has the DB)
        realized_pnl = 0.0
        wins = 0
        losses = 0
        total_trades = 0

        if use_local_pnl:
            all_trades = load_trades()
            total_trades = len(all_trades)
            for t in all_trades:
                if t.pnl is not None:
                    realized_pnl += t.pnl
                    if t.pnl > 0:
                        wins += 1
                    elif t.pnl < 0:
                        losses += 1

        return {
            "balance": balance,
            "positions": len(positions),
            "markets": len(all_markets),
            "recommendations": recs,
            "total_trades": total_trades,
            "realized_pnl": realized_pnl,
            "wins": wins,
            "losses": losses,
            "scanning": assets,
        }


# ── Star threshold → confidence mapping ──────────────────────

_STAR_MIN_CONFIDENCE = {5: 90, 4: 75, 3: 60, 2: 50, 1: 0}

# ── Autotrade dedup — tracks ticker+side → timestamp of last trade ──
# Prevents re-entering the same position while the market is still live.
_autotrade_dedup: dict[str, float] = {}
_DEDUP_TTL_SECONDS = 15 * 60  # one full expiry window


def _dedup_key(ticker: str, side: str) -> str:
    return f"{ticker}:{side}"


def _is_recently_traded(ticker: str, side: str) -> bool:
    import time
    key = _dedup_key(ticker, side)
    last = _autotrade_dedup.get(key)
    if last is None:
        return False
    return (time.time() - last) < _DEDUP_TTL_SECONDS


def _mark_traded(ticker: str, side: str) -> None:
    import time
    _autotrade_dedup[_dedup_key(ticker, side)] = time.time()
    # Prune stale entries
    now = time.time()
    stale = [k for k, t in _autotrade_dedup.items() if now - t >= _DEDUP_TTL_SECONDS]
    for k in stale:
        del _autotrade_dedup[k]


# ── Auto-trade (scan + execute eligible signals) ─────────────

async def run_autotrade(min_stars: int = 3) -> dict:
    """Scan markets and auto-execute all signals meeting the star threshold.

    Dedup: skips any ticker+side already traded within the last 15 minutes,
    preventing the same signal from being entered multiple times before expiry.

    Args:
        min_stars: Minimum star rating to execute (1-5). Default 3.

    Returns:
        Dict with scanned count, eligible count, traded list, skipped count, and errors.
    """
    min_confidence = _STAR_MIN_CONFIDENCE.get(min_stars, 60)

    raw = await run_scan(settle=False)
    recs: list[TradeRecommendation] = raw["recommendations"]

    eligible = [r for r in recs if r.confidence >= min_confidence]

    traded = []
    errors = []
    skipped = 0

    for rec in eligible:
        if _is_recently_traded(rec.ticker, rec.side.value):
            skipped += 1
            logger.debug(f"Autotrade dedup: skipping {rec.ticker} {rec.side.value} (already traded recently)")
            continue

        try:
            result = await run_trade(rec.ticker, rec.side.value, rec.price, rec.count)
            if result.get("success"):
                _mark_traded(rec.ticker, rec.side.value)
                traded.append({
                    "ticker": rec.ticker,
                    "side": rec.side.value,
                    "price": rec.price,
                    "count": rec.count,
                    "order_id": result.get("order_id", ""),
                })
            else:
                errors.append({"ticker": rec.ticker, "error": result.get("error", "unknown")})
        except Exception as e:
            errors.append({"ticker": rec.ticker, "error": str(e)})

    return {
        "scanned": len(recs),
        "eligible": len(eligible),
        "traded": traded,
        "skipped": skipped,
        "errors": errors,
        "min_stars": min_stars,
    }


# ── Standalone mode (reads .env) ─────────────────────────────

async def run_scan(
    assets: list[str] | None = None,
    settle: bool = True,
) -> dict:
    """Run scan in standalone mode — credentials from .env."""
    if assets is None:
        assets = list(ENABLED_ASSETS)
    client = KalshiClient.from_env()
    return await _scan_with_client(client, assets, settle, use_local_pnl=True)


async def run_trade(ticker: str, side: str, price: float, count: int) -> dict:
    """Execute a trade in standalone mode."""
    try:
        client = KalshiClient.from_env()
        return await _execute_trade(client, ticker, side, price, count, log_local=True)
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Multi-user mode (decrypts creds per-request) ─────────────

async def run_scan_for_user(
    api_key_enc: str,
    private_key_enc: str,
    assets: list[str] | None = None,
    settle: bool = True,
) -> dict:
    """Run scan for a specific user — decrypts Fernet-encrypted credentials."""
    if assets is None:
        assets = list(ENABLED_ASSETS)

    # Decrypt credentials in memory
    api_key = decrypt(api_key_enc)
    pem_content = decrypt(private_key_enc)

    client = KalshiClient.from_credentials(api_key, pem_content)
    return await _scan_with_client(client, assets, settle, use_local_pnl=False)


async def run_trade_for_user(
    api_key_enc: str,
    private_key_enc: str,
    ticker: str,
    side: str,
    price: float,
    count: int,
) -> dict:
    """Execute a trade for a specific user."""
    try:
        api_key = decrypt(api_key_enc)
        pem_content = decrypt(private_key_enc)
        client = KalshiClient.from_credentials(api_key, pem_content)
        return await _execute_trade(client, ticker, side, price, count, log_local=False)
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Shared trade execution ───────────────────────────────────

async def _execute_trade(
    client: KalshiClient,
    ticker: str,
    side: str,
    price: float,
    count: int,
    log_local: bool = True,
) -> dict:
    """Execute a trade with the given client."""
    async with client:
        price_cents = max(1, min(99, int(round(price * 100))))
        order = await client.create_order(
            ticker=ticker,
            side=side,
            price_cents=price_cents,
            count=count,
            order_type="limit",
            action="buy",
        )

        order_id = order.get("order_id", order.get("id", "unknown"))
        status_str = order.get("status", "pending").lower()
        fee = KalshiClient.estimate_fee(price, count)

        # Only log to local JSON in standalone mode
        if log_local:
            record = TradeRecord(
                id=order_id,
                ticker=ticker,
                side=Side(side),
                price=price,
                count=count,
                fee=fee,
                timestamp=datetime.now(timezone.utc).isoformat(),
                status=(
                    OrderStatus.FILLED
                    if status_str in ("executed", "filled")
                    else OrderStatus.PENDING
                ),
            )
            log_trade(record)

        return {
            "success": True,
            "order_id": order_id,
            "status": status_str,
            "ticker": ticker,
            "side": side,
            "price": price,
            "count": count,
            "fee": fee,
        }
