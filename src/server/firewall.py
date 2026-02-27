"""Strategy Firewall — THE WALL.

Converts raw TradeRecommendation objects into sanitized dicts
that reveal NOTHING about strategy internals.

FORT KNOX: No fair_value, edge, ema, trend, rr_ratio, confidence (raw),
reason, strike, spot, Kelly, or any Black-Scholes parameters ever leave
this module.
"""

from __future__ import annotations

from ..models import Side, TradeRecommendation


# ── Signal strength mapping ──────────────────────────────────

_SIGNAL_TIERS = [
    (90, 5, "Elite Signal"),
    (75, 4, "Strong Signal"),
    (60, 3, "Good Signal"),
    (50, 2, "Signal Detected"),
    (0,  1, "Weak Signal"),
]


def _confidence_to_stars(confidence: int) -> int:
    """Convert raw 0-100 confidence to 1-5 star rating."""
    for threshold, stars, _ in _SIGNAL_TIERS:
        if confidence >= threshold:
            return stars
    return 1


def _confidence_to_label(confidence: int) -> str:
    """Convert raw 0-100 confidence to user-facing label."""
    for threshold, _, label in _SIGNAL_TIERS:
        if confidence >= threshold:
            return label
    return "Weak Signal"


def _format_time(minutes: float) -> str:
    """Format minutes as 'Xm Ys'."""
    total_seconds = int(minutes * 60)
    mins = total_seconds // 60
    secs = total_seconds % 60
    if mins > 0 and secs > 0:
        return f"{mins}m {secs}s"
    elif mins > 0:
        return f"{mins}m"
    return f"{secs}s"


# ── The Wall ─────────────────────────────────────────────────

def _trend_label(rec: TradeRecommendation) -> str:
    """Classify trade as With Trend / Counter Trend / Neutral — no internals exposed."""
    trend = rec.trend
    side = rec.side
    if trend == "neutral":
        return "Neutral"
    aligned = (
        (side == Side.YES and trend == "bullish") or
        (side == Side.NO and trend == "bearish")
    )
    return "With Trend" if aligned else "Counter Trend"


def sanitize_recommendation(rec: TradeRecommendation) -> dict:
    """Strip all strategy secrets from a recommendation.

    Returns a dict safe for user-facing API responses.
    """
    return {
        "id": rec.ticker,
        "asset": rec.asset,
        "direction": "LONG" if rec.side == Side.YES else "SHORT",
        "entry_price": round(rec.price, 2),
        "payout": round(1.0 - rec.price, 2),
        "size": rec.count,
        "time_left": _format_time(rec.minutes_left),
        "time_left_mins": round(rec.minutes_left, 1),
        "signal_strength": _confidence_to_stars(rec.confidence),
        "signal_label": _confidence_to_label(rec.confidence),
        "trend_label": _trend_label(rec),
    }


def sanitize_recommendations(recs: list[TradeRecommendation]) -> list[dict]:
    """Sanitize a list of recommendations."""
    return [sanitize_recommendation(r) for r in recs]


def sanitize_stats(
    balance: float,
    total_trades: int,
    realized_pnl: float,
    wins: int,
    losses: int,
    open_positions: int,
) -> dict:
    """Build safe stats dict — no strategy internals."""
    settled = wins + losses
    win_rate = (wins / settled * 100) if settled > 0 else 0.0
    return {
        "balance": round(balance, 2),
        "total_trades": total_trades,
        "realized_pnl": round(realized_pnl, 4),
        "win_rate": round(win_rate, 1),
        "wins": wins,
        "losses": losses,
        "open_positions": open_positions,
    }
