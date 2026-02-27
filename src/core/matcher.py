"""Match identical prediction markets across platforms.

Real-world market question formats we need to handle:

Kalshi:
- Ticker: "KXBTC-26FEB2317-T77499.99" → BTC above $77,499.99 on Feb 26
- Ticker: "KXBTC-26FEB2317-B77250"    → BTC between range on Feb 26
- Question: "Bitcoin price range on Feb 23, 2026?"

Polymarket:
- "Will the price of Bitcoin be above $74,000 on February 23?"
- "Will Bitcoin reach $150,000 in February?"
- "Will Bitcoin dip to $60,000 in February?"

Strategy:
1. Parse Kalshi tickers directly (more reliable than question text)
2. Parse Polymarket questions with regex
3. Match on (asset, direction, strike, date)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from ..models import Market, MatchedMarket, Platform

logger = logging.getLogger(__name__)


@dataclass
class ParsedCryptoMarket:
    """Structured representation of a crypto prediction market."""
    asset: str           # "BTC" or "ETH"
    direction: str       # "above" or "below" or "reach" or "range"
    strike: float        # e.g., 97500.0
    expiry: str          # normalized date/time string for matching
    original: Market


def parse_crypto_market(market: Market) -> ParsedCryptoMarket | None:
    """Parse a crypto prediction market into structured components.

    Uses ticker for Kalshi, question text for Polymarket.
    """
    if market.platform == Platform.KALSHI:
        return _parse_kalshi(market)
    elif market.platform == Platform.POLYMARKET:
        return _parse_polymarket(market)
    return None


def _parse_kalshi(market: Market) -> ParsedCryptoMarket | None:
    """Parse Kalshi market from its ticker AND title.

    Ticker formats:
    - KXBTC-26FEB2317-T77499.99  → above threshold (short-term)
    - KXBTC-26FEB2317-B77250     → between/range bucket
    - KXBTCMAXMON-BTC-26FEB28-9750000 → above $97,500 by month end
    - KXETHMINMON-ETH-26FEB28-175000  → below $1,750 by month end
    - KXETHMAXY-27JAN01-5000.00       → above $5,000 by year end
    - KXBTC2026200-27JAN01-200000     → above $200,000 by year end

    Also parses title: "Will BTC trimmed mean be above $97500.00 by 11:59 PM ET on Feb 28, 2026?"
    """
    ticker = market.market_id
    title = market.question.lower()

    # Extract asset
    asset = None
    if "btc" in ticker.upper() or "bitcoin" in title:
        asset = "BTC"
    elif "eth" in ticker.upper() or "ethereum" in title:
        asset = "ETH"
    elif "sol" in ticker.upper() or "solana" in title:
        asset = "SOL"
    else:
        return None

    # Try parsing from title first (more reliable for newer series)
    title_parsed = _parse_from_title(title, asset, market)
    if title_parsed:
        return title_parsed

    # Fall back to ticker parsing
    # Extract date from ticker
    date_match = re.search(r"-(\d{1,2})([A-Z]{3})(\d{2,4})", ticker)
    expiry = "unknown"
    if date_match:
        day = date_match.group(1)
        month = date_match.group(2)
        rest = date_match.group(3)
        if len(rest) == 4:
            hour = rest[2:]
            expiry = f"{day}{month}-{hour}:00"
        elif len(rest) == 2:
            expiry = f"{day}{month}"
        else:
            expiry = f"{day}{month}{rest}"

    # Extract direction and strike
    threshold_match = re.search(r"-([\d.]+)$", ticker)
    if not threshold_match:
        return None

    raw_strike = threshold_match.group(1)
    strike = float(raw_strike)

    # Determine direction from ticker prefix or title
    direction = "above"  # default
    if "MAXMON" in ticker or "MAXY" in ticker:
        direction = "above"
    elif "MINMON" in ticker or "MINY" in ticker:
        direction = "below"
    elif "-B" in ticker:
        direction = "range"
    elif "-T" in ticker:
        direction = "above"

    # Handle series where strike is in cents (e.g., 9750000 = $97,500)
    if "MAXMON" in ticker or "MINMON" in ticker:
        if strike > 100000:
            strike = strike / 100  # Convert cents to dollars

    return ParsedCryptoMarket(
        asset=asset,
        direction=direction,
        strike=strike,
        expiry=expiry,
        original=market,
    )


def _parse_from_title(title: str, asset: str, market: Market) -> ParsedCryptoMarket | None:
    """Parse market from its human-readable title."""
    # Match: "Will X be above/below $Y by/on Z"
    above_match = re.search(r"(?:above|reach above|reach)\s+\$([\d,.]+)", title)
    below_match = re.search(r"(?:below|dip to|drop to|fall to)\s+\$([\d,.]+)", title)

    if above_match:
        strike = float(above_match.group(1).replace(",", ""))
        direction = "above"
    elif below_match:
        strike = float(below_match.group(1).replace(",", ""))
        direction = "below"
    else:
        return None

    if strike < 1:
        return None

    # Extract expiry from title
    expiry = "unknown"

    # "by 11:59 PM ET on Feb 28, 2026"
    date_in_title = re.search(
        r"(?:on|by)\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+(\d{1,2})",
        title,
    )
    if date_in_title:
        month_match = re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", title)
        if month_match:
            month_str = month_match.group(1).upper()[:3]
            day = date_in_title.group(1)
            expiry = f"{day}{month_str}"

    # "by Jan 1, 2027" → year-end
    year_match = re.search(r"(?:by|before)\s+jan(?:uary)?\s+1,?\s+(\d{4})", title)
    if year_match:
        year = year_match.group(1)
        expiry = f"end_{int(year)-1}"  # "by Jan 1 2027" = "by end of 2026"

    return ParsedCryptoMarket(
        asset=asset,
        direction=direction,
        strike=strike,
        expiry=expiry,
        original=market,
    )


def _parse_polymarket(market: Market) -> ParsedCryptoMarket | None:
    """Parse Polymarket question text into structured components.

    Formats:
    - "Will the price of Bitcoin be above $74,000 on February 23?"
    - "Will Bitcoin reach $150,000 in February?"
    - "Will Bitcoin dip to $60,000 in February?"
    - "Will Ethereum reach $5,000 in February?"
    """
    q = market.question

    # Extract asset
    asset = None
    q_lower = q.lower()
    if re.search(r"\b(btc|bitcoin)\b", q_lower):
        asset = "BTC"
    elif re.search(r"\b(eth|ethereum|ether)\b", q_lower):
        asset = "ETH"
    elif re.search(r"\b(sol|solana)\b", q_lower):
        asset = "SOL"
    else:
        return None

    # Extract strike price
    strike_match = re.search(r"\$([\d,]+(?:\.\d+)?)", q)
    if not strike_match:
        return None
    strike = float(strike_match.group(1).replace(",", ""))
    if strike < 50:
        return None

    # Extract direction
    direction = None
    if re.search(r"\b(above|over|higher than|at least)\b", q_lower):
        direction = "above"
    elif re.search(r"\b(below|under|lower than|dip to|drop to|fall to)\b", q_lower):
        direction = "below"
    elif re.search(r"\breach\b", q_lower):
        # "reach $X" is effectively "above $X"
        direction = "above"
    else:
        # Default: try to infer from context
        direction = "above"

    # Extract expiry date
    expiry = _extract_poly_expiry(q)

    return ParsedCryptoMarket(
        asset=asset,
        direction=direction,
        strike=strike,
        expiry=expiry,
        original=market,
    )


def _extract_poly_expiry(question: str) -> str:
    """Extract expiry from Polymarket question."""
    q = question.lower()

    # "on February 23" or "on Feb 23"
    date_match = re.search(
        r"on\s+(january|february|march|april|may|june|july|august|september|october|november|december|"
        r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{1,2})",
        q,
    )
    if date_match:
        month_str = date_match.group(1)[:3].upper()
        day = date_match.group(2)
        return f"{day}{month_str}"

    # "in February" (month-level, no specific day)
    month_match = re.search(
        r"in\s+(january|february|march|april|may|june|july|august|september|october|november|december)",
        q,
    )
    if month_match:
        month_str = month_match.group(1)[:3].upper()
        return f"month_{month_str}"

    # "by March 31" or "before April 1"
    by_match = re.search(
        r"(?:by|before)\s+(january|february|march|april|may|june|july|august|september|october|november|december|"
        r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{1,2})",
        q,
    )
    if by_match:
        month_str = by_match.group(1)[:3].upper()
        day = by_match.group(2)
        return f"{day}{month_str}"

    return "unknown"


def match_markets(
    markets_a: list[Market],
    markets_b: list[Market],
) -> list[MatchedMarket]:
    """Find matching markets across two platform market lists."""
    parsed_a = [p for m in markets_a if (p := parse_crypto_market(m)) is not None]
    parsed_b = [p for m in markets_b if (p := parse_crypto_market(m)) is not None]

    logger.info(
        f"Parsed {len(parsed_a)}/{len(markets_a)} from "
        f"{markets_a[0].platform.value if markets_a else '?'}, "
        f"{len(parsed_b)}/{len(markets_b)} from "
        f"{markets_b[0].platform.value if markets_b else '?'}"
    )

    # Build index on platform B for faster lookup
    b_by_key: dict[tuple, list[ParsedCryptoMarket]] = {}
    for pb in parsed_b:
        key = (pb.asset, pb.strike)
        b_by_key.setdefault(key, []).append(pb)

    matched: list[MatchedMarket] = []

    for pa in parsed_a:
        # Skip range/bucket markets for now (hard to match cross-platform)
        if pa.direction == "range":
            continue

        candidates = b_by_key.get((pa.asset, pa.strike), [])
        for pb in candidates:
            confidence = _match_score(pa, pb)
            if confidence >= 0.6:
                matched.append(MatchedMarket(
                    event_description=f"{pa.asset} {pa.direction} ${pa.strike:,.0f} @ {pa.expiry}",
                    market_a=pa.original,
                    market_b=pb.original,
                    match_confidence=confidence,
                ))

    # Deduplicate: keep highest confidence match per Polymarket market
    seen: dict[str, MatchedMarket] = {}
    for m in matched:
        key = f"{m.market_b.market_id}"
        if key not in seen or m.match_confidence > seen[key].match_confidence:
            seen[key] = m

    matched = list(seen.values())
    matched.sort(key=lambda m: m.match_confidence, reverse=True)

    logger.info(f"Found {len(matched)} matched markets across platforms")
    return matched


def _match_score(a: ParsedCryptoMarket, b: ParsedCryptoMarket) -> float:
    """Calculate match confidence between two parsed markets."""
    score = 0.0

    # Asset must match
    if a.asset != b.asset:
        return 0.0
    score += 0.25

    # Direction must be compatible
    if a.direction == b.direction:
        score += 0.25
    elif {a.direction, b.direction} <= {"above", "reach"}:
        score += 0.20  # "reach" ≈ "above"
    else:
        return 0.0

    # Strike must match exactly
    if abs(a.strike - b.strike) < 1.0:
        score += 0.30
    else:
        return 0.0

    # Expiry matching
    if a.expiry != "unknown" and b.expiry != "unknown":
        # Normalize for comparison
        a_norm = a.expiry.replace("-", "").replace(":", "").lower()
        b_norm = b.expiry.replace("-", "").replace(":", "").lower()
        if a_norm == b_norm:
            score += 0.20
        elif a_norm[:5] == b_norm[:5]:  # Same day, different time
            score += 0.10
    else:
        score += 0.05

    return round(score, 2)
