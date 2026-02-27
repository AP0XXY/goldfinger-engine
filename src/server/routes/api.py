"""API routes — all data passes through the firewall before leaving.

Supports two modes:
  - GET /scan?settle=0|1  → Standalone mode (reads .env)
  - POST /scan            → Multi-user mode (receives encrypted creds)
  - POST /trade           → Auto-detects mode from payload
  - GET /health           → Health check
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..scanner import run_scan, run_scan_for_user, run_trade, run_trade_for_user, sync_trades_for_user, run_autotrade
from ..firewall import sanitize_recommendations, sanitize_stats
from ...data.database import get_recent_signals, get_signal_stats

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic models for multi-user requests ──────────────────

class ScanRequest(BaseModel):
    user_id: str
    api_key_enc: str      # Fernet-encrypted Kalshi API key
    private_key_enc: str  # Fernet-encrypted PEM private key
    settle: bool = True


class TradeRequest(BaseModel):
    user_id: str
    api_key_enc: str
    private_key_enc: str
    ticker: str
    side: str
    price: float
    count: int


class SyncRequest(BaseModel):
    user_id: str
    api_key_enc: str
    private_key_enc: str
    trades: list[dict]  # [{id, order_id, ticker, side, price, count, fee}]


# ── Health check ─────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"status": "ok", "service": "goldfinger-engine"}


# ── Scan endpoints ───────────────────────────────────────────

@router.get("/scan")
async def scan_standalone(settle: int = Query(1, description="1=full scan, 0=light scan")):
    """Standalone scan — reads credentials from .env (single-user/admin)."""
    try:
        raw = await run_scan(settle=bool(settle))
        return _build_scan_response(raw)
    except Exception as e:
        logger.error(f"Standalone scan failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Scan failed. Check server logs."},
        )


@router.post("/scan")
async def scan_multiuser(req: ScanRequest):
    """Multi-user scan — receives encrypted credentials per-request."""
    try:
        raw = await run_scan_for_user(
            api_key_enc=req.api_key_enc,
            private_key_enc=req.private_key_enc,
            settle=req.settle,
        )
        return _build_scan_response(raw)
    except Exception as e:
        logger.error(f"Multi-user scan failed (user={req.user_id}): {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Scan failed. Check your API credentials."},
        )


def _build_scan_response(raw: dict) -> dict:
    """Pass raw results through THE WALL."""
    signals = sanitize_recommendations(raw["recommendations"])
    stats = sanitize_stats(
        balance=raw["balance"],
        total_trades=raw["total_trades"],
        realized_pnl=raw["realized_pnl"],
        wins=raw["wins"],
        losses=raw["losses"],
        open_positions=raw["positions"],
    )
    return {
        "signals": signals,
        "stats": stats,
        "scanning": raw["scanning"],
    }


# ── Trade endpoints ──────────────────────────────────────────

@router.post("/trade")
async def trade(payload: dict):
    """Execute a trade — auto-detects standalone vs multi-user mode."""
    try:
        # Multi-user mode: has encrypted credentials
        if "api_key_enc" in payload and "private_key_enc" in payload:
            req = TradeRequest(**payload)
            result = await run_trade_for_user(
                api_key_enc=req.api_key_enc,
                private_key_enc=req.private_key_enc,
                ticker=req.ticker,
                side=req.side,
                price=req.price,
                count=req.count,
            )
        else:
            # Standalone mode
            ticker = payload["ticker"]
            side = payload["side"]
            price = float(payload["price"])
            count = int(payload["count"])
            result = await run_trade(
                ticker=ticker,
                side=side,
                price=price,
                count=count,
            )

        return result

    except KeyError as e:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": f"Missing field: {e}"},
        )
    except Exception as e:
        logger.error(f"Trade failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


# ── Sync endpoint (check settlements) ────────────────────────

@router.post("/sync-trades")
async def sync_trades(req: SyncRequest):
    """Check Kalshi for settlement results on user's pending trades."""
    try:
        updates = await sync_trades_for_user(
            api_key_enc=req.api_key_enc,
            private_key_enc=req.private_key_enc,
            trades=req.trades,
        )
        return {"updates": updates}
    except Exception as e:
        logger.error(f"Trade sync failed (user={req.user_id}): {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Sync failed."},
        )


# ── Auto-trade endpoint ──────────────────────────────────────

@router.get("/autotrade")
async def autotrade_standalone(min_stars: int = Query(3, ge=1, le=5, description="Minimum star rating to execute")):
    """Scan and auto-execute all signals meeting the star threshold (standalone mode)."""
    try:
        result = await run_autotrade(min_stars=min_stars)
        return result
    except Exception as e:
        logger.error(f"Autotrade failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Autotrade failed. Check server logs."},
        )


# ── Signal history endpoints ────────────────────────────────

@router.get("/signals")
async def get_signals(limit: int = Query(100, description="Number of signals to return"), profitable_only: bool = Query(False)):
    """Get recent arbitrage signals from persistent storage."""
    try:
        signals = get_recent_signals(limit=limit, profitable_only=profitable_only)
        return {
            "count": len(signals),
            "signals": [s.to_dict() for s in signals],
        }
    except Exception as e:
        logger.error(f"Failed to fetch signals: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to fetch signals"},
        )


@router.get("/signals/stats")
async def get_signals_stats():
    """Get statistics about stored signals."""
    try:
        stats = get_signal_stats()
        return stats
    except Exception as e:
        logger.error(f"Failed to fetch signal stats: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to fetch signal stats"},
        )
