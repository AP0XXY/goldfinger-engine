"""Goldfinger Dashboard — FastAPI entry point.

Usage:
    python -m src.dashboard
    python -m src.dashboard --port 8050
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Goldfinger Dashboard")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Ensure data directories exist
    for d in ("data/spreads", "data/scans", "data/pnl"):
        Path(d).mkdir(parents=True, exist_ok=True)

    # Import here to avoid circular imports
    import uvicorn
    from .server.app import app

    print(f"\n  GOLDFINGER running at http://localhost:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
