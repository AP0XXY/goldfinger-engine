"""Data collection and spread logging for historical analysis."""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path

from ..models import ArbitrageOpportunity, MatchedMarket

logger = logging.getLogger(__name__)


class SpreadLogger:
    """Logs spread data to CSV for analysis."""

    def __init__(self, data_dir: str = "data/spreads"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._csv_path = self.data_dir / f"spreads_{datetime.utcnow().strftime('%Y%m%d')}.csv"
        self._init_csv()

    def _init_csv(self):
        if not self._csv_path.exists():
            with open(self._csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp",
                    "event",
                    "platform_a",
                    "platform_b",
                    "yes_ask_a",
                    "no_ask_a",
                    "yes_ask_b",
                    "no_ask_b",
                    "best_gross_spread",
                    "estimated_fees",
                    "net_spread",
                    "net_spread_pct",
                    "is_profitable",
                ])

    def log_opportunity(self, opp: ArbitrageOpportunity):
        """Log a detected arbitrage opportunity."""
        with open(self._csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                opp.timestamp.isoformat(),
                opp.matched_market.event_description,
                opp.buy_yes_platform.value,
                opp.buy_no_platform.value,
                opp.buy_yes_price,
                "",  # no_ask_a not directly stored
                "",  # yes_ask_b not directly stored
                opp.buy_no_price,
                opp.gross_spread,
                opp.estimated_fees,
                opp.net_spread,
                opp.net_spread_pct,
                opp.is_profitable,
            ])

    def log_matched_spread(self, matched: MatchedMarket):
        """Log spread data for a matched market (even if not arbitrageable)."""
        a = matched.market_a
        b = matched.market_b

        # Calculate the best possible spread
        a_yes = a.yes_price or 0
        b_no = b.no_price or (1.0 - (b.yes_price or 1.0))
        spread_1 = 1.0 - (a_yes + b_no) if a_yes and b_no else None

        b_yes = b.yes_price or 0
        a_no = a.no_price or (1.0 - (a.yes_price or 1.0))
        spread_2 = 1.0 - (b_yes + a_no) if b_yes and a_no else None

        best_spread = max(spread_1 or -999, spread_2 or -999)

        with open(self._csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.utcnow().isoformat(),
                matched.event_description,
                a.platform.value,
                b.platform.value,
                a.yes_price,
                a.no_price,
                b.yes_price,
                b.no_price,
                round(best_spread, 4) if best_spread > -999 else "",
                "",
                "",
                "",
                best_spread > 0 if best_spread > -999 else "",
            ])


class ScanResultLogger:
    """Logs full scan results as JSON for detailed analysis."""

    def __init__(self, data_dir: str = "data/scans"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def log_scan(
        self,
        matched_count: int,
        opportunities: list[ArbitrageOpportunity],
        kalshi_count: int,
        poly_count: int,
    ):
        ts = datetime.utcnow()
        result = {
            "timestamp": ts.isoformat(),
            "kalshi_markets": kalshi_count,
            "polymarket_markets": poly_count,
            "matched_markets": matched_count,
            "opportunities_found": len(opportunities),
            "opportunities": [
                {
                    "event": o.matched_market.event_description,
                    "buy_yes": o.buy_yes_platform.value,
                    "buy_yes_price": o.buy_yes_price,
                    "buy_no": o.buy_no_platform.value,
                    "buy_no_price": o.buy_no_price,
                    "gross_spread": o.gross_spread,
                    "fees": o.estimated_fees,
                    "net_spread": o.net_spread,
                    "net_pct": o.net_spread_pct,
                }
                for o in opportunities
            ],
        }

        path = self.data_dir / f"scan_{ts.strftime('%Y%m%d_%H%M%S')}.json"
        with open(path, "w") as f:
            json.dump(result, f, indent=2)

        logger.info(f"Scan results saved to {path}")
