# Goldfinger Engine

Standalone prediction market arbitrage scanner for Kalshi 15-minute crypto binary options.

Black-Scholes d2 pricing model with EMA-20 trend confirmation. Scans BTC and ETH markets, identifies mispriced contracts, and executes trades via the Kalshi API.

## Quick Start

```bash
# 1. Clone and enter
git clone <repo-url> goldfinger-engine
cd goldfinger-engine

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure credentials
cp .env.example .env
# Edit .env with your Kalshi API key
# Place your RSA private key at config/kalshi_private_key.pem

# 5. Run the dashboard
python -m src.dashboard
```

Open **http://localhost:8050** in your browser.

## Commands

| Command | Description |
|---------|-------------|
| `python -m src.dashboard` | Start the web dashboard on port 8050 |
| `python -m src.dashboard --port 9000` | Start on a custom port |
| `python -m src.main` | One-shot scan (CLI output) |
| `python -m src.main --loop --interval 30` | Continuous scanning every 30s |
| `python -m src.hft` | Interactive HFT mode with trade approval |
| `python -m src.hft --asset ETH` | HFT for a specific asset |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Dashboard UI |
| `/api/scan?settle=1` | GET | Run a full scan |
| `/api/scan?settle=0` | GET | Light scan (no settlement check) |
| `/api/trade` | POST | Execute a trade |
| `/api/health` | GET | Health check |

## Configuration

**`.env`** — Kalshi API credentials:
```
KALSHI_API_KEY=your-api-key-uuid
KALSHI_PRIVATE_KEY_PATH=config/kalshi_private_key.pem
```

**`config/settings.yaml`** — Arbitrage thresholds, platform URLs, dashboard settings.

## Project Structure

```
src/
  dashboard.py          # Entry point (FastAPI + Uvicorn)
  models.py             # Shared data models
  core/
    strategy.py         # Black-Scholes pricing + signal generation
    arbitrage.py        # Arbitrage detection
  exchanges/
    kalshi.py           # Kalshi async API client
  server/
    app.py              # FastAPI app factory
    scanner.py          # Scan orchestration
    firewall.py         # Response sanitization
    routes/
      api.py            # /api/scan, /api/trade
      dashboard.py      # GET / (renders HTML)
  static/               # CSS + JS
  templates/             # Jinja2 HTML template
  data/
    pnl.py              # Trade history + P&L tracking
config/
  settings.yaml         # App configuration
data/
  pnl/trades.json       # Trade history (auto-created)
```

## Strategy (v3.1)

- **Pricing:** Black-Scholes d2 binary option model
- **Trend:** EMA-20 from 1-minute Coinbase candles
- **Assets:** BTC (vol 0.80), ETH (vol 0.90)
- **Filters:** min edge $0.04, max price $0.35, min confidence 40, R/R >= 2.0
- **Data:** CoinGecko spot prices (30s cache), Coinbase candles, Kalshi orderbooks
