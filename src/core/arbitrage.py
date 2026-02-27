"""Arbitrage detection engine.

Core logic: For a binary market, YES + NO must pay out exactly $1.00.
If we can buy YES on Platform A and NO on Platform B for less than $1.00 combined,
the difference (minus fees) is risk-free profit.

Example:
  Kalshi YES ask:     $0.52
  Polymarket NO ask:  $0.45
  Total cost:         $0.97
  Guaranteed payout:  $1.00
  Gross profit:       $0.03 (3.09%)
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..models import (
    ArbitrageOpportunity,
    Market,
    MatchedMarket,
    Platform,
)
from ..exchanges.kalshi import KalshiClient
from ..exchanges.polymarket import PolymarketClient

logger = logging.getLogger(__name__)


def detect_arbitrage(
    matched: MatchedMarket,
    min_spread: float = 0.02,
    min_spread_pct: float = 1.5,
) -> ArbitrageOpportunity | None:
    """Check a matched market pair for arbitrage opportunity.

    We check both directions:
    1. Buy YES on A + Buy NO on B
    2. Buy YES on B + Buy NO on A

    Uses ask prices (what we'd actually pay to buy).
    """
    a = matched.market_a
    b = matched.market_b

    # Get effective ask prices
    # For YES: use yes_ask from orderbook, or fall back to yes_price
    a_yes_ask = _get_yes_ask(a)
    a_no_ask = _get_no_ask(a)
    b_yes_ask = _get_yes_ask(b)
    b_no_ask = _get_no_ask(b)

    best_opp = None

    # Direction 1: Buy YES on A, Buy NO on B
    if a_yes_ask is not None and b_no_ask is not None:
        opp = _evaluate_direction(
            matched=matched,
            yes_platform=a.platform,
            yes_price=a_yes_ask,
            no_platform=b.platform,
            no_price=b_no_ask,
            yes_market=a,
            no_market=b,
        )
        if opp and opp.net_spread >= min_spread and opp.net_spread_pct >= min_spread_pct:
            best_opp = opp

    # Direction 2: Buy YES on B, Buy NO on A
    if b_yes_ask is not None and a_no_ask is not None:
        opp = _evaluate_direction(
            matched=matched,
            yes_platform=b.platform,
            yes_price=b_yes_ask,
            no_platform=a.platform,
            no_price=a_no_ask,
            yes_market=b,
            no_market=a,
        )
        if opp and opp.net_spread >= min_spread and opp.net_spread_pct >= min_spread_pct:
            if best_opp is None or opp.net_spread > best_opp.net_spread:
                best_opp = opp

    return best_opp


def _evaluate_direction(
    matched: MatchedMarket,
    yes_platform: Platform,
    yes_price: float,
    no_platform: Platform,
    no_price: float,
    yes_market: Market,
    no_market: Market,
) -> ArbitrageOpportunity | None:
    """Evaluate a specific arbitrage direction."""
    total_cost = yes_price + no_price

    if total_cost >= 1.0:
        return None  # No arbitrage possible

    gross_spread = 1.0 - total_cost

    # Estimate fees for each side
    yes_fee = _estimate_fee(yes_market, yes_price)
    no_fee = _estimate_fee(no_market, no_price)
    total_fees = yes_fee + no_fee

    net_spread = gross_spread - total_fees
    net_pct = (net_spread / total_cost * 100) if total_cost > 0 else 0

    return ArbitrageOpportunity(
        matched_market=matched,
        buy_yes_platform=yes_platform,
        buy_yes_price=round(yes_price, 4),
        buy_no_platform=no_platform,
        buy_no_price=round(no_price, 4),
        gross_spread=round(gross_spread, 4),
        estimated_fees=round(total_fees, 4),
        net_spread=round(net_spread, 4),
        net_spread_pct=round(net_pct, 2),
        timestamp=datetime.utcnow(),
    )


def _estimate_fee(market: Market, price: float) -> float:
    """Estimate trading fee based on platform."""
    if market.platform == Platform.KALSHI:
        return KalshiClient.estimate_fee(price, count=1)
    elif market.platform == Platform.POLYMARKET:
        # Only crypto micro-markets have fees on Polymarket
        if PolymarketClient.is_crypto_micro_market(market.question):
            return PolymarketClient.estimate_fee(price, count=1)
        return 0.0
    return 0.0


def _get_yes_ask(market: Market) -> float | None:
    """Get the best YES ask price (cheapest to buy YES)."""
    if market.orderbook and market.orderbook.best_yes_ask is not None:
        return market.orderbook.best_yes_ask
    # Fall back: if we have no_price, yes_ask ≈ 1 - no_price
    if market.no_price is not None:
        return round(1.0 - market.no_price, 4)
    # Fall back: use yes_price as approximation
    return market.yes_price


def _get_no_ask(market: Market) -> float | None:
    """Get the best NO ask price (cheapest to buy NO)."""
    if market.orderbook and market.orderbook.best_no_ask is not None:
        return market.orderbook.best_no_ask
    # Fall back: if we have yes_price, no_ask ≈ 1 - yes_price
    if market.yes_price is not None:
        return round(1.0 - market.yes_price, 4)
    return market.no_price


def scan_all_opportunities(
    matched_markets: list[MatchedMarket],
    min_spread: float = 0.02,
    min_spread_pct: float = 1.5,
) -> list[ArbitrageOpportunity]:
    """Scan all matched markets for arbitrage opportunities."""
    opportunities = []
    for matched in matched_markets:
        opp = detect_arbitrage(matched, min_spread, min_spread_pct)
        if opp:
            opportunities.append(opp)

    # Sort by net spread descending
    opportunities.sort(key=lambda o: o.net_spread, reverse=True)
    return opportunities
