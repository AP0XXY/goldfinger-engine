"""Shared data models for prediction market arbitrage."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class Platform(str, enum.Enum):
    KALSHI = "kalshi"
    POLYMARKET = "polymarket"
    WEBULL = "webull"


class MarketStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"
    SETTLED = "settled"


class Side(str, enum.Enum):
    YES = "yes"
    NO = "no"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    PARTIAL = "partial"


@dataclass
class PriceLevel:
    price: float  # 0.00 to 1.00
    size: float   # quantity available


@dataclass
class OrderBook:
    yes_bids: list[PriceLevel] = field(default_factory=list)
    yes_asks: list[PriceLevel] = field(default_factory=list)
    timestamp: datetime | None = None

    @property
    def best_yes_bid(self) -> float | None:
        return max((l.price for l in self.yes_bids), default=None)

    @property
    def best_yes_ask(self) -> float | None:
        return min((l.price for l in self.yes_asks), default=None)

    @property
    def best_no_bid(self) -> float | None:
        ask = self.best_yes_ask
        return (1.0 - ask) if ask is not None else None

    @property
    def best_no_ask(self) -> float | None:
        bid = self.best_yes_bid
        return (1.0 - bid) if bid is not None else None

    @property
    def mid_price(self) -> float | None:
        bid, ask = self.best_yes_bid, self.best_yes_ask
        if bid is not None and ask is not None:
            return (bid + ask) / 2
        return None

    @property
    def spread(self) -> float | None:
        bid, ask = self.best_yes_bid, self.best_yes_ask
        if bid is not None and ask is not None:
            return ask - bid
        return None


@dataclass
class Market:
    platform: Platform
    market_id: str          # platform-specific ID (ticker for Kalshi, condition_id for Polymarket)
    question: str           # human-readable description
    status: MarketStatus
    yes_price: float | None = None
    no_price: float | None = None
    last_price: float | None = None
    volume: float | None = None
    volume_24h: float | None = None
    close_time: datetime | None = None
    orderbook: OrderBook | None = None
    # Platform-specific metadata
    raw: dict = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        return f"[{self.platform.value}] {self.question}"


@dataclass
class MatchedMarket:
    """Same event matched across two platforms."""
    event_description: str
    market_a: Market
    market_b: Market
    match_confidence: float = 0.0  # 0.0 to 1.0

    @property
    def platforms(self) -> tuple[Platform, Platform]:
        return (self.market_a.platform, self.market_b.platform)


@dataclass
class ArbitrageOpportunity:
    """A detected arbitrage opportunity across two platforms."""
    matched_market: MatchedMarket
    buy_yes_platform: Platform
    buy_yes_price: float
    buy_no_platform: Platform
    buy_no_price: float
    gross_spread: float       # 1.00 - (yes_price + no_price)
    estimated_fees: float     # combined fees on both sides
    net_spread: float         # gross_spread - estimated_fees
    net_spread_pct: float     # net_spread / cost * 100
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def cost(self) -> float:
        return self.buy_yes_price + self.buy_no_price

    @property
    def is_profitable(self) -> bool:
        return self.net_spread > 0

    def __str__(self) -> str:
        return (
            f"ARB: {self.matched_market.event_description}\n"
            f"  Buy YES on {self.buy_yes_platform.value} @ ${self.buy_yes_price:.4f}\n"
            f"  Buy NO  on {self.buy_no_platform.value} @ ${self.buy_no_price:.4f}\n"
            f"  Cost: ${self.cost:.4f} -> Payout: $1.00\n"
            f"  Gross: ${self.gross_spread:.4f} | Fees: ${self.estimated_fees:.4f} | Net: ${self.net_spread:.4f} ({self.net_spread_pct:.2f}%)"
        )


@dataclass
class TradeRecommendation:
    """A recommended trade from the strategy engine."""
    ticker: str
    side: Side
    price: float           # limit price in dollars (0.01 - 0.99)
    count: int             # number of contracts
    edge: float            # fair_value - price (for buys) or price - fair_value (for sells)
    fair_value: float      # model fair value
    minutes_left: float    # minutes until market resolves
    strike: float          # strike price (e.g. 97500 for BTC)
    spot: float            # current spot price
    reason: str            # human-readable explanation
    # Enhanced strategy fields:
    confidence: int = 0           # 0-100 confidence score
    trend: str = "neutral"        # "bullish", "bearish", "neutral"
    rr_ratio: float = 0.0        # reward/risk ratio (e.g. 9.0 = 9:1)
    ema: float | None = None     # EMA-20 value
    asset: str = ""              # BTC, ETH, SOL


@dataclass
class TradeRecord:
    """A completed or pending trade."""
    id: str                # order ID from exchange
    ticker: str
    side: Side
    price: float
    count: int
    fee: float
    timestamp: str         # ISO format
    pnl: float | None = None        # realized PnL (None if not yet settled)
    status: OrderStatus = OrderStatus.PENDING
    settled_price: float | None = None  # 1.0 or 0.0 after settlement

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ticker": self.ticker,
            "side": self.side.value,
            "price": self.price,
            "count": self.count,
            "fee": self.fee,
            "timestamp": self.timestamp,
            "pnl": self.pnl,
            "status": self.status.value,
            "settled_price": self.settled_price,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TradeRecord":
        return cls(
            id=d["id"],
            ticker=d["ticker"],
            side=Side(d["side"]),
            price=d["price"],
            count=d["count"],
            fee=d["fee"],
            timestamp=d["timestamp"],
            pnl=d.get("pnl"),
            status=OrderStatus(d.get("status", "pending")),
            settled_price=d.get("settled_price"),
        )


@dataclass
class AccountSnapshot:
    """Summary of account state."""
    balance: float
    open_positions: int
    realized_pnl: float
    unrealized_pnl: float
    total_trades: int
    wins: int
    losses: int

    @property
    def win_rate(self) -> float:
        settled = self.wins + self.losses
        return (self.wins / settled * 100) if settled > 0 else 0.0
