"""Kalshi prediction market API client."""

from __future__ import annotations

import base64
import logging
import time
from datetime import datetime
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ..models import Market, MarketStatus, OrderBook, Platform, PriceLevel

logger = logging.getLogger(__name__)

DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"
PROD_BASE = "https://api.elections.kalshi.com/trade-api/v2"


class KalshiClient:
    """Async client for the Kalshi trading API."""

    def __init__(
        self,
        api_key: str | None = None,
        private_key_path: str | None = None,
        base_url: str = DEMO_BASE,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._private_key = None

        if private_key_path:
            pem_data = Path(private_key_path).read_bytes()
            self._private_key = serialization.load_pem_private_key(pem_data, password=None)

        self._client = httpx.AsyncClient(timeout=30.0)

    @classmethod
    def from_env(cls, base_url: str = PROD_BASE) -> "KalshiClient":
        """Create an authenticated client from .env file."""
        from dotenv import dotenv_values
        config = dotenv_values(".env")
        return cls(
            api_key=config.get("KALSHI_API_KEY"),
            private_key_path=config.get("KALSHI_PRIVATE_KEY_PATH"),
            base_url=base_url,
        )

    @classmethod
    def from_credentials(
        cls,
        api_key: str,
        pem_content: str,
        base_url: str = PROD_BASE,
    ) -> "KalshiClient":
        """Create client from raw credentials (multi-user mode).

        Args:
            api_key: Kalshi API key ID (UUID).
            pem_content: PEM private key as a string (not a file path).
            base_url: API base URL.
        """
        instance = cls(api_key=api_key, base_url=base_url)
        pem_bytes = pem_content.encode() if isinstance(pem_content, str) else pem_content
        instance._private_key = serialization.load_pem_private_key(
            pem_bytes, password=None
        )
        return instance

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    # ── Authentication ──────────────────────────────────────────────

    def _sign(self, timestamp_ms: str, method: str, path: str) -> str:
        """Create RSA-PSS signature for request authentication."""
        if not self._private_key:
            raise RuntimeError("Private key required for authenticated requests")

        path_no_query = path.split("?")[0]
        message = f"{timestamp_ms}{method}{path_no_query}".encode()

        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode()

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        return {
            "KALSHI-ACCESS-KEY": self.api_key or "",
            "KALSHI-ACCESS-SIGNATURE": self._sign(ts, method, path),
            "KALSHI-ACCESS-TIMESTAMP": ts,
        }

    # ── HTTP helpers ────────────────────────────────────────────────

    def _full_path(self, url: str) -> str:
        """Extract the full URL path for signature (e.g. /trade-api/v2/...)."""
        return url.replace("https://api.elections.kalshi.com", "").replace("https://demo-api.kalshi.co", "")

    async def _get(self, path: str, params: dict | None = None, auth: bool = False) -> dict:
        url = f"{self.base_url}{path}"
        full_path = self._full_path(url)
        headers = self._auth_headers("GET", full_path) if auth else {}
        resp = await self._client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, body: dict | None = None, auth: bool = True) -> dict:
        url = f"{self.base_url}{path}"
        full_path = self._full_path(url)
        headers = self._auth_headers("POST", full_path) if auth else {}
        headers["Content-Type"] = "application/json"
        resp = await self._client.post(url, json=body or {}, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _delete(self, path: str, auth: bool = True) -> dict:
        url = f"{self.base_url}{path}"
        full_path = self._full_path(url)
        headers = self._auth_headers("DELETE", full_path) if auth else {}
        resp = await self._client.delete(url, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # ── Market discovery ────────────────────────────────────────────

    async def get_markets(
        self,
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        status: str = "open",
        limit: int = 200,
    ) -> list[Market]:
        """Fetch markets, optionally filtered by series/event ticker."""
        params: dict = {"status": status, "limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker

        all_markets: list[Market] = []
        cursor = None

        while True:
            if cursor:
                params["cursor"] = cursor
            data = await self._get("/markets", params=params)
            raw_markets = data.get("markets", [])

            for m in raw_markets:
                all_markets.append(self._parse_market(m))

            cursor = data.get("cursor")
            if not cursor or len(raw_markets) < limit:
                break

        logger.info(f"Kalshi: fetched {len(all_markets)} markets (series={series_ticker})")
        return all_markets

    async def get_crypto_markets(self) -> list[Market]:
        """Fetch all open BTC and ETH prediction markets across all series."""
        import asyncio

        # Core price-range series
        core_series = ["KXBTC", "KXETH"]
        # Threshold/one-touch series
        extra_series = [
            "KXBTCMAXMON",  # BTC monthly max (above X by month end)
            "KXBTCMINMON",  # BTC monthly min (below X by month end) -- may exist
            "KXETHMAXY",    # ETH yearly high
            "KXETHMINMON",  # ETH monthly min
            "KXBTC2026200", # BTC above $200K by 2027
        ]

        tasks = [self.get_markets(series_ticker=s) for s in core_series + extra_series]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_markets = []
        for r in results:
            if isinstance(r, list):
                all_markets.extend(r)
        return all_markets

    async def get_orderbook(self, ticker: str, depth: int = 10) -> OrderBook:
        """Fetch orderbook for a specific market."""
        data = await self._get(f"/markets/{ticker}/orderbook", params={"depth": depth})

        book_data = data.get("orderbook_fp", data.get("orderbook", {}))
        yes_bids_raw = book_data.get("yes_dollars", book_data.get("yes", []))
        no_bids_raw = book_data.get("no_dollars", book_data.get("no", []))

        yes_bids = [PriceLevel(price=float(p), size=float(s)) for p, s in yes_bids_raw]
        # NO bids at price X = YES asks at price (1 - X)
        yes_asks = [PriceLevel(price=round(1.0 - float(p), 4), size=float(s)) for p, s in no_bids_raw]

        return OrderBook(
            yes_bids=sorted(yes_bids, key=lambda x: x.price, reverse=True),
            yes_asks=sorted(yes_asks, key=lambda x: x.price),
            timestamp=datetime.utcnow(),
        )

    async def get_market_with_book(self, ticker: str) -> Market:
        """Fetch a single market with its orderbook."""
        data = await self._get(f"/markets/{ticker}")
        market = self._parse_market(data.get("market", data))
        market.orderbook = await self.get_orderbook(ticker)
        return market

    # ── Portfolio / Account ──────────────────────────────────────────

    async def get_balance(self) -> float:
        """Get account balance in dollars."""
        data = await self._get("/portfolio/balance", auth=True)
        # API returns balance in cents — convert to dollars
        balance_cents = data.get("balance", 0)
        return float(balance_cents) / 100.0

    async def get_positions(self) -> list[dict]:
        """Get open positions."""
        data = await self._get("/portfolio/positions", auth=True)
        positions = data.get("market_positions", data.get("positions", []))
        return positions

    async def get_open_orders(self) -> list[dict]:
        """Get open/resting orders."""
        data = await self._get("/portfolio/orders", auth=True)
        return data.get("orders", [])

    async def create_order(
        self,
        ticker: str,
        side: str,  # "yes" or "no"
        price_cents: int,  # 1-99
        count: int = 1,
        order_type: str = "limit",
        action: str = "buy",
    ) -> dict:
        """Place an order on Kalshi.

        Args:
            ticker: Market ticker (e.g. "KXBTC15M-26FEB231500")
            side: "yes" or "no"
            price_cents: Limit price in cents (1-99)
            count: Number of contracts
            order_type: "limit" or "market"
            action: "buy" or "sell"
        """
        body = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "type": order_type,
        }
        if order_type == "limit":
            body["yes_price"] = price_cents
        data = await self._post("/portfolio/orders", body=body)
        order = data.get("order", data)
        logger.info(f"Kalshi order placed: {side} {count}x {ticker} @ {price_cents}c -> {order.get('order_id', 'unknown')}")
        return order

    async def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order."""
        data = await self._delete(f"/portfolio/orders/{order_id}")
        logger.info(f"Kalshi order cancelled: {order_id}")
        return data

    # ── 15-Minute crypto markets ─────────────────────────────────────

    async def get_15min_markets(
        self, asset: str = "BTC", max_windows: int = 1
    ) -> list[Market]:
        """Fetch upcoming 15-minute crypto prediction markets.

        Series tickers: KXBTC15M, KXETH15M, KXSOL15M

        Args:
            asset: Crypto symbol.
            max_windows: Number of expiry windows to include (1=nearest only,
                         3=next ~45 minutes of markets).
        """
        series_map = {"BTC": "KXBTC15M", "ETH": "KXETH15M", "SOL": "KXSOL15M"}
        series = series_map.get(asset.upper())
        if not series:
            logger.warning(f"Unknown asset {asset}, trying KXBTC15M")
            series = "KXBTC15M"

        markets = await self.get_markets(series_ticker=series, status="open")
        markets.sort(key=lambda m: m.close_time or datetime.max)

        if max_windows > 1 and markets:
            from itertools import groupby
            groups = []
            for _close_time, group_iter in groupby(
                markets, key=lambda m: m.close_time
            ):
                groups.append(list(group_iter))
                if len(groups) >= max_windows:
                    break
            markets = [m for group in groups for m in group]

        logger.info(f"Kalshi: {len(markets)} open {series} markets ({max_windows} windows)")
        return markets

    # ── Spot price ───────────────────────────────────────────────────

    # CoinGecko ID mapping (symbol → coingecko id)
    _COINGECKO_IDS: dict[str, str] = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
    }
    # Coinbase product mapping (symbol → product ID)
    _COINBASE_PRODUCTS: dict[str, str] = {
        "BTC": "BTC-USD",
        "ETH": "ETH-USD",
    }

    # In-memory spot price cache (shared across instances)
    _spot_cache: dict[str, float] = {}
    _spot_cache_time: float = 0.0
    _SPOT_CACHE_TTL: float = 30.0  # seconds — avoid CoinGecko 429 rate limits

    async def get_spot_prices(self, assets: list[str] | None = None) -> dict[str, float]:
        """Fetch spot prices using CoinGecko (free, no API key needed).

        Results are cached for 30s to respect CoinGecko's free-tier rate limit.
        """
        if assets is None:
            assets = ["BTC", "ETH"]

        # Return cached prices if still fresh
        now = time.time()
        if (now - self._spot_cache_time) < self._SPOT_CACHE_TTL and self._spot_cache:
            cached = {k: v for k, v in self._spot_cache.items() if k in [a.upper() for a in assets]}
            if len(cached) == len(assets):
                logger.info(f"Spot prices (cached): {cached}")
                return cached

        # Map asset symbols to CoinGecko IDs
        ids = []
        asset_map: dict[str, str] = {}  # coingecko_id → our symbol
        for a in assets:
            key = a.upper()
            cg_id = self._COINGECKO_IDS.get(key, key.lower())
            ids.append(cg_id)
            asset_map[cg_id] = key

        url = (
            f"https://api.coingecko.com/api/v3/simple/price"
            f"?ids={','.join(ids)}&vs_currencies=usd"
        )
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and self._spot_cache:
                logger.warning("CoinGecko rate limited (429), using cached prices")
                return {k: v for k, v in self._spot_cache.items() if k in [a.upper() for a in assets]}
            raise

        prices = {}
        for cg_id, symbol in asset_map.items():
            if cg_id in data and "usd" in data[cg_id]:
                prices[symbol] = float(data[cg_id]["usd"])
                logger.info(f"Spot {symbol}: ${prices[symbol]:,.2f}")

        # Update cache
        KalshiClient._spot_cache.update(prices)
        KalshiClient._spot_cache_time = now
        return prices

    async def get_spot_price(self, asset: str = "BTC") -> float:
        """Fetch current spot price for a single asset."""
        prices = await self.get_spot_prices([asset.upper()])
        return prices.get(asset.upper(), 0.0)

    async def get_candles(
        self, asset: str = "BTC", limit: int = 24
    ) -> list[dict]:
        """Fetch recent 1-minute candles from Coinbase (free, no key, US-accessible).

        Returns list of dicts with keys: time, open, high, low, close.
        Ordered oldest-first. limit=24 gives 25 data points (enough for EMA-20).
        """
        product = self._COINBASE_PRODUCTS.get(asset.upper(), f"{asset.upper()}-USD")
        url = (
            f"https://api.exchange.coinbase.com/products/{product}/candles"
            f"?granularity=60"
        )
        resp = await self._client.get(url)
        resp.raise_for_status()
        raw = resp.json()

        # Coinbase format: [time, low, high, open, close, volume] — newest first
        # Take only what we need and reverse to oldest-first
        entries = raw[: limit + 1]
        entries.reverse()

        candles = [
            {
                "time": int(k[0]),
                "open": float(k[3]),
                "high": float(k[2]),
                "low": float(k[1]),
                "close": float(k[4]),
            }
            for k in entries
        ]
        logger.info(f"Fetched {len(candles)} 1-min candles for {asset}")
        return candles

    # ── Fee calculation ─────────────────────────────────────────────

    @staticmethod
    def estimate_fee(price: float, count: int = 1) -> float:
        """Estimate Kalshi trading fee.

        Formula: fee = 0.07 * count * price * (1 - price)
        Capped at $0.02 per contract, minimum $0.01.
        """
        raw = 0.07 * count * price * (1 - price)
        per_contract = min(max(raw / count, 0.01), 0.02) if count > 0 else 0
        return round(per_contract * count, 4)

    # ── Parsing ─────────────────────────────────────────────────────

    @staticmethod
    def _parse_market(m: dict) -> Market:
        yes_bid = m.get("yes_bid_dollars") or m.get("yes_bid")
        yes_ask = m.get("yes_ask_dollars") or m.get("yes_ask")
        last = m.get("last_price_dollars") or m.get("last_price")

        close_time = None
        if m.get("close_time"):
            try:
                close_time = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        status_str = m.get("status", "open")
        status_map = {
            "open": MarketStatus.OPEN,
            "closed": MarketStatus.CLOSED,
            "settled": MarketStatus.SETTLED,
        }

        return Market(
            platform=Platform.KALSHI,
            market_id=m.get("ticker", ""),
            question=m.get("title", m.get("yes_sub_title", m.get("ticker", ""))),
            status=status_map.get(status_str, MarketStatus.OPEN),
            yes_price=_to_float(yes_bid),
            no_price=round(1.0 - _to_float(yes_ask), 4) if yes_ask else None,
            last_price=_to_float(last),
            volume=_to_float(m.get("volume_fp", m.get("volume"))),
            volume_24h=_to_float(m.get("volume_24h_fp", m.get("volume_24h"))),
            close_time=close_time,
            raw=m,
        )


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
