"""Dashboard HTML route — serves the Goldfinger UI."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()

PNL_DIR = Path("data/pnl")
TRADES_FILE = PNL_DIR / "trades.json"


def _load_trades() -> list[dict]:
    """Load raw trade dicts for template rendering."""
    if not TRADES_FILE.exists():
        return []
    try:
        with open(TRADES_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, Exception):
        return []


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Serve the main Goldfinger dashboard."""
    trades = _load_trades()

    # Compute PnL stats for initial page render
    realized_pnl = 0.0
    wins = 0
    losses = 0
    for t in trades:
        pnl = t.get("pnl")
        if pnl is not None:
            realized_pnl += pnl
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
    settled = wins + losses
    win_rate = (wins / settled * 100) if settled > 0 else 0.0

    # Chart data (cumulative PnL)
    chart_labels = []
    chart_cum_pnl = []
    chart_trade_pnl = []
    cum = 0.0
    for t in trades:
        chart_labels.append(t.get("timestamp", "")[:16])
        pnl_val = t.get("pnl")
        if pnl_val is not None:
            cum += pnl_val
            chart_trade_pnl.append(round(pnl_val, 4))
        else:
            chart_trade_pnl.append(0)
        chart_cum_pnl.append(round(cum, 4))

    # Trade history (last 30, reversed for newest-first)
    trade_history = list(reversed(trades[-30:]))

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "realized_pnl": round(realized_pnl, 2),
            "total_trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 1),
            "trade_history": trade_history,
            "chart_labels": json.dumps(chart_labels),
            "chart_cum_pnl": json.dumps(chart_cum_pnl),
            "chart_trade_pnl": json.dumps(chart_trade_pnl),
        },
    )
