"""Strategy engine for Kalshi 15-minute crypto prediction markets.

Prices binary options using a log-normal model (Black-Scholes d2 term)
and finds mispriced contracts vs the market.

v3.2 — Counter-trend penalty raised from -12 to -30:
  - Counter-trend shorts were dominating and losing (17 NO vs 5 YES in first session)
  - -30 penalty means counter-trend only passes with large edge + perfect R/R + timing
  - Trend-aligned and neutral signals unchanged
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone

from scipy.stats import norm

from ..models import Market, Side, TradeRecommendation

logger = logging.getLogger(__name__)

# ── Data-driven constants ─────────────────────────────────────────────

# Annualized volatility estimates — RAISED significantly.
# Previous: BTC 0.60, ETH 0.70 → model under-priced large moves,
# creating false edges (especially on NO side). Higher vol → fair values
# closer to 50% → only genuine mispricings survive.
DEFAULT_VOL = {"BTC": 0.80, "ETH": 0.90}

# Minimum edge (dollars) to recommend a trade.
# v3.1: 0.04 — 4c is meaningful but not so strict that nothing passes.
MIN_EDGE = 0.04

# Max contract price — better asymmetry at low prices.
# At $0.35 you need ~37% accuracy to break even.
MAX_PRICE = 0.35

# Minimum reward/risk ratio.
# 2.0:1 keeps good asymmetry while actually being reachable at MAX_PRICE.
MIN_REWARD_RISK = 2.0

# Confidence score threshold.
# v3.1: 40 — v3's 55 was unreachable without perfect trend+edge alignment.
MIN_CONFIDENCE = 40

# Time window: markets expiring in this range get max time points
TIME_SWEET_SPOT = (5.0, 12.0)

# Minimum minutes before expiry — raised to avoid last-minute noise
MIN_MINUTES = 3.0

# EMA period
EMA_PERIOD = 20

# Kelly fractions — dramatically reduced while calibrating.
# Previous: BTC 0.20, ETH 0.12. Now tiny until model proves itself.
KELLY_BY_ASSET = {"BTC": 0.08, "ETH": 0.06}
KELLY_DEFAULT = 0.06

# Max contracts per trade — hard cap to limit exposure
MAX_CONTRACTS = 10

# Assets to scan — BTC + ETH only
ENABLED_ASSETS = {"BTC", "ETH"}


# ── Pricing model ─────────────────────────────────────────────────────

def fair_value_binary(spot: float, strike: float, minutes_left: float, vol: float) -> float:
    """Compute fair value of a binary "above strike" option.

    Uses the Black-Scholes d2 term:
        d2 = (ln(S/K) - 0.5 * sigma^2 * T) / (sigma * sqrt(T))
        P(above) = N(d2)
    """
    if minutes_left <= 0:
        return 1.0 if spot >= strike else 0.0

    if strike <= 0 or spot <= 0:
        return 0.5

    T = minutes_left / (365.25 * 24 * 60)  # years
    sqrt_T = math.sqrt(T)

    if sqrt_T * vol < 1e-10:
        return 1.0 if spot >= strike else 0.0

    d2 = (math.log(spot / strike) - 0.5 * vol**2 * T) / (vol * sqrt_T)
    return float(norm.cdf(d2))


# ── EMA + trend ───────────────────────────────────────────────────────

def compute_ema(candles: list[dict], period: int = EMA_PERIOD) -> float | None:
    """Compute EMA on close prices from candle data.

    Args:
        candles: List of candle dicts with 'close' key, oldest first.
        period: EMA period (default 20).

    Returns:
        Current EMA value, or None if insufficient data.
    """
    closes = [c.get("close", 0) for c in candles if c.get("close")]
    if len(closes) < period:
        return None

    multiplier = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period  # seed with SMA
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def trend_direction(spot: float, ema: float | None) -> str:
    """Determine trend direction from spot vs EMA.

    Returns: 'bullish', 'bearish', or 'neutral'.
    Uses 0.25% buffer to avoid false trend reads from minor fluctuations.
    """
    if ema is None:
        return "neutral"
    pct_diff = (spot - ema) / ema
    if pct_diff > 0.0025:   # clearly above EMA
        return "bullish"
    elif pct_diff < -0.0025:  # clearly below EMA
        return "bearish"
    return "neutral"


# ── Risk/reward + confidence ──────────────────────────────────────────

def reward_risk_ratio(price: float) -> float:
    """Reward/risk for a binary option at given price.

    YES at $0.10: win $0.90, risk $0.10 → 9:1
    YES at $0.25: win $0.75, risk $0.25 → 3:1
    """
    if price <= 0 or price >= 1.0:
        return 0.0
    return (1.0 - price) / price


def compute_confidence(
    edge: float,
    trend: str,
    rr: float,
    minutes_left: float,
    side: Side = Side.YES,
) -> int:
    """Score a trade opportunity 0-100.

    v3.2 — Counter-trend penalty raised:
      Edge size:      0-30 pts  (4c+ starts scoring)
      EMA alignment:  0-25 pts  (trend-aligned gets full bonus)
      Risk/reward:    0-20 pts
      Time window:    0-15 pts
      Counter-trend:  -30 pts penalty (needs large edge + perfect conditions)
      Edge bonus:     +10 pts for very large edges (>10c)
    """
    score = 0

    # Edge (0-30): edge 0.06 = 6pts, 0.10 = 10pts, 0.15+ = 30pts
    # Scale: divide by 0.01 so 6c edge = 6pts
    score += min(30, int(edge / 0.01))

    # Extra bonus for large edge (>10c = likely genuine mispricing)
    if edge >= 0.10:
        score += 10

    # EMA alignment (0-25) — direction-aware
    aligned = (
        (side == Side.YES and trend == "bullish") or
        (side == Side.NO and trend == "bearish")
    )
    counter = (
        (side == Side.YES and trend == "bearish") or
        (side == Side.NO and trend == "bullish")
    )

    if aligned:
        score += 25
    elif trend == "neutral":
        score += 10
    elif counter:
        # Penalty — -30 means only exceptional counter-trend signals pass
        # (requires large edge + great R/R + perfect timing to reach 40)
        score -= 30

    # Risk/reward (0-20)
    if rr >= 10:
        score += 20
    elif rr >= 5:
        score += 15
    elif rr >= 3:
        score += 10
    elif rr >= 2.5:
        score += 7

    # Time window (0-15) — sweet spot is 5-12 min
    if TIME_SWEET_SPOT[0] <= minutes_left <= TIME_SWEET_SPOT[1]:
        score += 15
    elif 3.0 <= minutes_left < TIME_SWEET_SPOT[0]:
        score += 8
    elif TIME_SWEET_SPOT[1] < minutes_left <= 14.5:
        score += 10

    return max(0, min(100, score))


# ── Market parsing helpers ────────────────────────────────────────────

def parse_ticker_strike(ticker: str) -> float | None:
    """Extract strike price from a KXBTC15M ticker."""
    match = re.search(r"(\d{3,6}(?:\.\d+)?)", ticker)
    if match:
        return float(match.group(1))
    return None


def extract_strike_from_market(market: Market) -> float | None:
    """Extract the strike price from a market's question/title or raw data."""
    raw = market.raw
    for field in ("floor_strike", "strike_price", "floor_value"):
        if raw.get(field) is not None:
            try:
                return float(raw[field])
            except (ValueError, TypeError):
                pass

    text = market.question or ""
    patterns = [
        r"\$?([\d,]+(?:\.\d+)?)\s*\?",
        r"above\s+\$?([\d,]+(?:\.\d+)?)",
        r"below\s+\$?([\d,]+(?:\.\d+)?)",
        r"between\s+\$?([\d,]+).*?\$?([\d,]+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = m.group(1).replace(",", "")
            try:
                return float(val)
            except ValueError:
                pass

    sub = raw.get("yes_sub_title", "")
    m = re.search(r"\$?([\d,]+(?:\.\d+)?)", sub)
    if m:
        val = m.group(1).replace(",", "")
        try:
            return float(val)
        except ValueError:
            pass

    return None


def detect_direction(market: Market) -> str:
    """Detect if market is 'above' or 'below' type. Default: 'above'."""
    text = (market.question or "").lower()
    if "below" in text or "under" in text or "less than" in text:
        return "below"
    return "above"


def detect_asset(market: Market) -> str:
    """Detect asset from market ticker or question."""
    ticker = market.market_id.upper()
    if "BTC" in ticker or "BITCOIN" in ticker:
        return "BTC"
    if "ETH" in ticker or "ETHEREUM" in ticker:
        return "ETH"
    q = (market.question or "").upper()
    if "BTC" in q or "BITCOIN" in q:
        return "BTC"
    if "ETH" in q or "ETHEREUM" in q:
        return "ETH"
    return "BTC"


def minutes_until(close_time: datetime | None) -> float:
    """Minutes from now until close_time."""
    if close_time is None:
        return 15.0
    now = datetime.now(timezone.utc)
    if close_time.tzinfo is None:
        close_time = close_time.replace(tzinfo=timezone.utc)
    delta = (close_time - now).total_seconds() / 60
    return max(delta, 0.0)


# ── Main strategy ─────────────────────────────────────────────────────

def find_opportunities(
    markets: list[Market],
    spot_prices: dict[str, float],
    balance: float = 0.0,
    ema_data: dict[str, float | None] | None = None,
) -> list[TradeRecommendation]:
    """Scan markets for mispriced contracts — evaluates BOTH sides of every market.

    v3.1 rebalanced — kept vol fix, relaxed combined filters that were
    too strict together (v3 effectively blocked all signals).

    Filters:
    1. Asset filter:  BTC + ETH only
    2. Time filter:   Min 3 min to expiry
    3. Price cap:     Max $0.35 (breakeven at ~37% win rate)
    4. Edge:          Min 4 cents mispricing
    5. R/R:           Min 2.0:1
    6. Confidence:    Score ≥ 40
    7. Position cap:  Max 10 contracts
    8. Per-ticker:    Keep best opportunity per market
    """
    recs: list[TradeRecommendation] = []

    for market in markets:
        asset = detect_asset(market)

        # Gate 1: Asset filter
        if asset not in ENABLED_ASSETS:
            continue

        spot = spot_prices.get(asset)
        if spot is None:
            continue

        strike = extract_strike_from_market(market)
        if strike is None:
            logger.debug(f"Skipping {market.market_id}: can't extract strike")
            continue

        mins_left = minutes_until(market.close_time)
        if mins_left < MIN_MINUTES:
            continue  # too close to resolution

        vol = DEFAULT_VOL.get(asset, 0.65)
        direction = detect_direction(market)

        # Fair value of YES side
        fv_yes = fair_value_binary(spot, strike, mins_left, vol)
        if direction == "below":
            fv_yes = 1.0 - fv_yes
        fv_no = 1.0 - fv_yes

        # Determine trend from EMA
        ema_val = (ema_data or {}).get(asset)
        trend = trend_direction(spot, ema_val)

        raw = market.raw

        # ── Evaluate YES side ──
        yes_ask_raw = raw.get("yes_ask_dollars") or raw.get("yes_ask")
        yes_price = _to_float(yes_ask_raw) or market.yes_price
        if yes_price is not None and yes_price > 1.0:
            yes_price = yes_price / 100.0

        if yes_price is not None and 0 < yes_price <= MAX_PRICE:
            yes_edge = fv_yes - yes_price
            yes_rr = reward_risk_ratio(yes_price)
            yes_conf = compute_confidence(yes_edge, trend, yes_rr, mins_left, side=Side.YES)

            if yes_edge >= MIN_EDGE and yes_rr >= MIN_REWARD_RISK and yes_conf >= MIN_CONFIDENCE:
                kelly_frac = KELLY_BY_ASSET.get(asset, KELLY_DEFAULT)
                count = position_size(yes_edge, balance, yes_price, kelly_frac)
                trend_emoji = "↑" if trend == "bullish" else "→" if trend == "neutral" else "↓"

                recs.append(TradeRecommendation(
                    ticker=market.market_id,
                    side=Side.YES,
                    price=yes_price,
                    count=count,
                    edge=round(yes_edge, 4),
                    fair_value=round(fv_yes, 4),
                    minutes_left=round(mins_left, 1),
                    strike=strike,
                    spot=spot,
                    reason=(
                        f"BUY YES: fair={fv_yes:.2%} vs ask={yes_price:.2%}, "
                        f"edge={yes_edge:.2%}. {asset} ${spot:,.0f} {trend_emoji} strike "
                        f"${strike:,.0f}, {mins_left:.0f}min left"
                    ),
                    confidence=yes_conf,
                    trend=trend,
                    rr_ratio=round(yes_rr, 1),
                    ema=ema_val,
                    asset=asset,
                ))

        # ── Evaluate NO side ──
        no_ask_raw = raw.get("no_ask_dollars") or raw.get("no_ask")
        no_price = _to_float(no_ask_raw)

        # Fallback: NO ask ≈ 1 - YES bid
        if no_price is None or no_price > 1.0:
            yes_bid_raw = raw.get("yes_bid_dollars") or raw.get("yes_bid")
            yes_bid = _to_float(yes_bid_raw)
            if yes_bid is not None and 0 < yes_bid < 1.0:
                no_price = round(1.0 - yes_bid, 4)
            elif market.yes_price is not None and 0 < market.yes_price < 1.0:
                no_price = round(1.0 - market.yes_price, 4)

        if no_price is not None and no_price > 1.0:
            no_price = no_price / 100.0

        if no_price is not None and 0 < no_price <= MAX_PRICE:
            no_edge = fv_no - no_price
            no_rr = reward_risk_ratio(no_price)
            no_conf = compute_confidence(no_edge, trend, no_rr, mins_left, side=Side.NO)

            if no_edge >= MIN_EDGE and no_rr >= MIN_REWARD_RISK and no_conf >= MIN_CONFIDENCE:
                kelly_frac = KELLY_BY_ASSET.get(asset, KELLY_DEFAULT)
                count = position_size(no_edge, balance, no_price, kelly_frac)
                trend_emoji = "↓" if trend == "bearish" else "→" if trend == "neutral" else "↑"

                recs.append(TradeRecommendation(
                    ticker=market.market_id,
                    side=Side.NO,
                    price=no_price,
                    count=count,
                    edge=round(no_edge, 4),
                    fair_value=round(fv_no, 4),
                    minutes_left=round(mins_left, 1),
                    strike=strike,
                    spot=spot,
                    reason=(
                        f"BUY NO: fair={fv_no:.2%} vs ask={no_price:.2%}, "
                        f"edge={no_edge:.2%}. {asset} ${spot:,.0f} {trend_emoji} strike "
                        f"${strike:,.0f}, {mins_left:.0f}min left"
                    ),
                    confidence=no_conf,
                    trend=trend,
                    rr_ratio=round(no_rr, 1),
                    ema=ema_val,
                    asset=asset,
                ))

    # Per-ticker dedup: keep highest confidence per ticker
    best_per_ticker: dict[str, TradeRecommendation] = {}
    for rec in recs:
        existing = best_per_ticker.get(rec.ticker)
        if existing is None or rec.confidence > existing.confidence:
            best_per_ticker[rec.ticker] = rec
    recs = list(best_per_ticker.values())

    # Sort by confidence descending, then edge
    recs.sort(key=lambda r: (r.confidence, r.edge), reverse=True)
    return recs


def position_size(
    edge: float, balance: float, price: float, kelly_fraction: float = KELLY_DEFAULT
) -> int:
    """Position sizing using asset-specific fractional Kelly.

    v3 — Reduced fractions + hard cap at MAX_CONTRACTS.
    BTC uses 8% Kelly, ETH uses 6% Kelly.
    """
    if balance <= 0 or price <= 0 or price >= 1.0:
        return 1

    odds = (1.0 - price) / price
    kelly = edge / (price * odds) if odds > 0 else 0
    fraction = kelly * kelly_fraction
    dollars = balance * fraction
    count = max(1, int(dollars / price))
    # Hard cap to limit single-trade exposure
    count = min(count, MAX_CONTRACTS)
    return count


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
