"""Background arbitrage scanner for continuous monitoring."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from ..exchanges.kalshi import KalshiClient
from ..exchanges.polymarket import PolymarketClient
from ..core.matcher import match_markets
from ..core.arbitrage import scan_all_opportunities
from ..data.database import save_signal

logger = logging.getLogger(__name__)


async def background_scanner(interval: int = 30, min_spread: float = 0.02, min_spread_pct: float = 1.5):
    """Continuously scan for arbitrage opportunities in background."""
    logger.info(f"Starting background scanner (interval: {interval}s)")
    
    scan_count = 0
    kalshi_url = "https://api.elections.kalshi.com/trade-api/v2"
    
    try:
        async with KalshiClient(base_url=kalshi_url) as kalshi, PolymarketClient() as poly:
            while True:
                scan_count += 1
                timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                logger.info(f"[Scan #{scan_count}] Starting arbitrage scan at {timestamp}")
                
                try:
                    # Fetch markets concurrently
                    kalshi_markets, poly_markets = await asyncio.gather(
                        kalshi.get_crypto_markets(),
                        poly.get_crypto_markets(),
                        return_exceptions=True
                    )
                    
                    # Check for errors
                    if isinstance(kalshi_markets, Exception):
                        logger.error(f"Kalshi fetch failed: {kalshi_markets}")
                        await asyncio.sleep(interval)
                        continue
                    if isinstance(poly_markets, Exception):
                        logger.error(f"Polymarket fetch failed: {poly_markets}")
                        await asyncio.sleep(interval)
                        continue
                    
                    if not kalshi_markets or not poly_markets:
                        logger.debug("Not enough markets to scan, skipping")
                        await asyncio.sleep(interval)
                        continue
                    
                    logger.debug(f"Fetched {len(kalshi_markets)} Kalshi and {len(poly_markets)} Polymarket markets")
                    
                    # Match markets
                    matched = match_markets(kalshi_markets, poly_markets)
                    logger.debug(f"Found {len(matched)} matched market pairs")
                    
                    # Detect arbitrage
                    opportunities = scan_all_opportunities(matched, min_spread, min_spread_pct)
                    
                    if opportunities:
                        logger.info(f"Found {len(opportunities)} arbitrage opportunities!")
                        
                        # Save all opportunities to database
                        for opp in opportunities:
                            try:
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
                            except Exception as e:
                                logger.error(f"Failed to save signal: {e}")
                    else:
                        logger.debug("No arbitrage opportunities found this scan")
                    
                except Exception as e:
                    logger.error(f"Scan error: {e}", exc_info=True)
                
                await asyncio.sleep(interval)
                
    except asyncio.CancelledError:
        logger.info("Background scanner stopped")
    except Exception as e:
        logger.error(f"Background scanner fatal error: {e}", exc_info=True)
