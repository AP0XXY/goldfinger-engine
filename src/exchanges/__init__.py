from .kalshi import KalshiClient

__all__ = ["KalshiClient"]

# Polymarket client is optional (requires eth_account / py-clob-client)
try:
    from .polymarket import PolymarketClient
    __all__.append("PolymarketClient")
except ImportError:
    pass
