"""Polymarket CLOB API client."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json as json_mod
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime

import httpx
from eth_account import Account

from ..models import Market, MarketStatus, OrderBook, Platform, PriceLevel

logger = logging.getLogger(__name__)

CLOB_URL = "https://clob.polymarket.com"
GAMMA_URL = "https://gamma-api.polymarket.com"
DATA_URL = "https://data-api.polymarket.com"


@dataclass
class PolymarketPosition:
    """A position on Polymarket."""
    market_question: str
    condition_id: str
    token_id: str
    side: str  # "YES" or "NO"
    size: float
    avg_price: float
    current_price: float | None = None
    pnl: float | None = None


class PolymarketClient:
    """Async client for Polymarket with optional authentication."""

    def __init__(
        self,
        clob_url: str = CLOB_URL,
        gamma_url: str = GAMMA_URL,
        private_key: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_passphrase: str | None = None,
        address: str | None = None,
    ):
        self.clob_url = clob_url.rstrip("/")
        self.gamma_url = gamma_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)

        # Auth credentials
        self._private_key = private_key
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase
        self._address: str | None = address

        # Derive wallet address from private key (if provided)
        if private_key and not self._address:
            try:
                acct = Account.from_key(private_key)
                self._address = acct.address
            except Exception as e:
                logger.warning(f"Could not derive address from private key: {e}")

        if self._address:
            logger.info(f"Polymarket wallet: {self._address[:6]}...{self._address[-4:]}")

    @classmethod
    def from_env(cls, clob_url: str = CLOB_URL, gamma_url: str = GAMMA_URL) -> "PolymarketClient":
        """Create an authenticated client from .env file."""
        from dotenv import dotenv_values
        config = dotenv_values(".env")
        return cls(
            clob_url=clob_url,
            gamma_url=gamma_url,
            private_key=config.get("POLYMARKET_PRIVATE_KEY"),
            api_key=config.get("POLYMARKET_API_KEY"),
            api_secret=config.get("POLYMARKET_SECRET"),
            api_passphrase=config.get("POLYMARKET_PASSPHRASE"),
            address=config.get("POLYMARKET_ADDRESS"),
        )

    @property
    def is_authenticated(self) -> bool:
        return all([self._api_key, self._api_secret, self._api_passphrase, self._address])

    @property
    def address(self) -> str | None:
        return self._address

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    # ── L2 HMAC Authentication ─────────────────────────────────────

    def _l2_headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        """Build L2 HMAC-SHA256 authentication headers."""
        if not self.is_authenticated:
            raise RuntimeError("API credentials required for authenticated requests")

        timestamp = str(int(time.time()))
        msg = f"{timestamp}{method}{path}{body}".encode()
        secret_bytes = base64.urlsafe_b64decode(self._api_secret)
        sig = base64.urlsafe_b64encode(
            hmac.new(secret_bytes, msg, hashlib.sha256).digest()
        ).decode()

        return {
            "POLY_ADDRESS": self._address,
            "POLY_SIGNATURE": sig,
            "POLY_TIMESTAMP": timestamp,
            "POLY_API_KEY": self._api_key,
            "POLY_PASSPHRASE": self._api_passphrase,
        }

    async def _auth_get(self, path: str, params: dict | None = None) -> dict:
        """Authenticated GET request to CLOB API."""
        url = f"{self.clob_url}{path}"
        headers = self._l2_headers("GET", path)
        resp = await self._client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _auth_post(self, path: str, body: dict | None = None) -> dict:
        """Authenticated POST request to CLOB API."""
        body_str = json_mod.dumps(body) if body else ""
        url = f"{self.clob_url}{path}"
        headers = self._l2_headers("POST", path, body_str)
        headers["Content-Type"] = "application/json"
        resp = await self._client.post(url, content=body_str, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _auth_delete(self, path: str, body: dict | None = None) -> dict:
        """Authenticated DELETE request to CLOB API."""
        body_str = json_mod.dumps(body) if body else ""
        url = f"{self.clob_url}{path}"
        headers = self._l2_headers("DELETE", path, body_str)
        headers["Content-Type"] = "application/json"
        resp = await self._client.request("DELETE", url, content=body_str, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # ── Account & Portfolio ────────────────────────────────────────

    async def get_balance(self) -> dict:
        """Get USDC balance and allowances."""
        try:
            return await self._auth_get("/balance-allowance", params={"asset_type": "USDC"})
        except Exception:
            return {"balance": "unknown", "note": "Balance requires CTF Exchange setup"}

    async def get_open_orders(self, market: str | None = None) -> list[dict]:
        """Get open orders, optionally filtered by market."""
        params = {}
        if market:
            params["market"] = market
        data = await self._auth_get("/data/orders", params=params)
        return data if isinstance(data, list) else data.get("orders", data.get("data", []))

    async def get_trades(self, maker_address: str | None = None) -> list[dict]:
        """Get recent trades."""
        params = {}
        if maker_address:
            params["maker_address"] = maker_address
        elif self._address:
            params["maker_address"] = self._address
        data = await self._auth_get("/data/trades", params=params)
        return data if isinstance(data, list) else data.get("trades", data.get("data", []))

    async def get_positions(self) -> list[dict]:
        """Get current positions from the data API."""
        if not self._address:
            raise RuntimeError("Wallet address required")
        resp = await self._client.get(
            f"{DATA_URL}/positions",
            params={"user": self._address.lower()},
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("positions", data.get("data", []))

    async def get_pnl(self) -> dict:
        """Get profit/loss summary."""
        if not self._address:
            raise RuntimeError("Wallet address required")
        resp = await self._client.get(
            f"{DATA_URL}/pnl",
            params={"user": self._address.lower()},
        )
        resp.raise_for_status()
        return resp.json()

    async def cancel_order(self, order_id: str) -> dict:
        """Cancel a specific order."""
        return await self._auth_delete("/order", body={"orderID": order_id})

    async def cancel_all_orders(self) -> dict:
        """Cancel all open orders."""
        return await self._auth_delete("/cancel-all")

    # ── Market discovery (Gamma API) ───────────────────────────────

    async def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        closed: bool = False,
        order: str = "volume24hr",
        ascending: bool = False,
    ) -> list[Market]:
        """Fetch markets from the Gamma API."""
        params = {
            "limit": limit,
            "offset": offset,
            "closed": str(closed).lower(),
            "order": order,
            "ascending": str(ascending).lower(),
        }
        resp = await self._client.get(f"{self.gamma_url}/markets", params=params)
        resp.raise_for_status()
        raw_markets = resp.json()

        markets = []
        for m in raw_markets:
            parsed = self._parse_market(m)
            if parsed:
                markets.append(parsed)

        logger.info(f"Polymarket: fetched {len(markets)} markets")
        return markets

    async def get_crypto_markets(self, keywords: list[str] | None = None) -> list[Market]:
        """Fetch crypto prediction markets by searching for keywords."""
        if keywords is None:
            keywords = ["Bitcoin", "BTC", "Ethereum", "ETH"]

        all_markets: list[Market] = []
        seen_ids: set[str] = set()

        # Fetch high-volume open markets and filter
        offset = 0
        while offset < 500:  # scan up to 500 markets
            batch = await self.get_markets(limit=100, offset=offset)
            if not batch:
                break

            for m in batch:
                if m.market_id in seen_ids:
                    continue
                question_lower = m.question.lower()
                if any(kw.lower() in question_lower for kw in keywords):
                    seen_ids.add(m.market_id)
                    all_markets.append(m)

            offset += 100

        logger.info(f"Polymarket: found {len(all_markets)} crypto markets")
        return all_markets

    # ── Orderbook (CLOB API) ───────────────────────────────────────

    async def get_orderbook(self, token_id: str) -> OrderBook:
        """Fetch orderbook for a specific token from the CLOB."""
        resp = await self._client.get(f"{self.clob_url}/book", params={"token_id": token_id})
        resp.raise_for_status()
        data = resp.json()

        bids = [PriceLevel(price=float(b["price"]), size=float(b["size"])) for b in data.get("bids", [])]
        asks = [PriceLevel(price=float(a["price"]), size=float(a["size"])) for a in data.get("asks", [])]

        return OrderBook(
            yes_bids=sorted(bids, key=lambda x: x.price, reverse=True),
            yes_asks=sorted(asks, key=lambda x: x.price),
            timestamp=datetime.utcnow(),
        )

    async def get_midpoint(self, token_id: str) -> float | None:
        """Get the midpoint price for a token."""
        resp = await self._client.get(f"{self.clob_url}/midpoint", params={"token_id": token_id})
        resp.raise_for_status()
        data = resp.json()
        mid = data.get("mid") or data.get("mid_price")
        return float(mid) if mid else None

    async def get_price(self, token_id: str, side: str = "BUY") -> float | None:
        """Get the best available price for a side (BUY or SELL)."""
        resp = await self._client.get(
            f"{self.clob_url}/price",
            params={"token_id": token_id, "side": side},
        )
        resp.raise_for_status()
        data = resp.json()
        price = data.get("price")
        return float(price) if price else None

    # ── Fee calculation ─────────────────────────────────────────────

    @staticmethod
    def estimate_fee(price: float, count: int = 1, fee_rate: float = 0.0025, exponent: int = 2) -> float:
        """Estimate Polymarket trading fee for crypto micro-markets.

        For 5-min/15-min crypto markets: fee_rate=0.0025, exponent=2
        For most other markets: fee is 0 (zero).

        Formula: fee = count * fee_rate * (price * (1 - price))^exponent
        """
        raw = count * fee_rate * (price * (1 - price)) ** exponent
        return round(raw, 6)

    @staticmethod
    def is_crypto_micro_market(question: str) -> bool:
        """Check if a market is a crypto micro-market (which has fees)."""
        q = question.lower()
        return bool(
            re.search(r"\b(btc|bitcoin|eth|ethereum)\b", q)
            and re.search(r"\b(5.?min|15.?min|5.?minute|15.?minute)\b", q)
        )

    # ── Parsing ─────────────────────────────────────────────────────

    @staticmethod
    def _parse_market(m: dict) -> Market | None:
        clob_ids = m.get("clobTokenIds", [])
        # clobTokenIds may be a JSON string instead of a list
        if isinstance(clob_ids, str):
            try:
                import json
                clob_ids = json.loads(clob_ids)
            except (json.JSONDecodeError, TypeError):
                return None
        if not clob_ids:
            return None

        outcome_prices = m.get("outcomePrices", [])
        # outcomePrices may also be a JSON string
        if isinstance(outcome_prices, str):
            try:
                import json
                outcome_prices = json.loads(outcome_prices)
            except (json.JSONDecodeError, TypeError):
                outcome_prices = []

        yes_price = _to_float(outcome_prices[0]) if len(outcome_prices) > 0 else None
        no_price = _to_float(outcome_prices[1]) if len(outcome_prices) > 1 else None

        return Market(
            platform=Platform.POLYMARKET,
            market_id=m.get("conditionId", m.get("id", "")),
            question=m.get("question", ""),
            status=MarketStatus.CLOSED if m.get("closed") else MarketStatus.OPEN,
            yes_price=yes_price,
            no_price=no_price,
            last_price=_to_float(m.get("lastTradePrice")),
            volume=_to_float(m.get("volume")),
            volume_24h=_to_float(m.get("volume24hr")),
            raw=m,
        )


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
