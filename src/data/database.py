"""SQLite database for persistent arbitrage signal storage."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import Column, DateTime, Float, Integer, String, create_engine, func, cast
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger(__name__)

# Setup database
DB_DIR = Path("data/db")
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "signals.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class ArbitrageSignal(Base):
    """Persistent record of detected arbitrage opportunity."""
    __tablename__ = "arbitrage_signals"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    
    # Event/Market info
    event_description = Column(String, index=True)
    
    # Buy YES/NO strategy
    buy_yes_platform = Column(String)  # "KALSHI" or "POLYMARKET"
    buy_yes_price = Column(Float)
    buy_no_platform = Column(String)
    buy_no_price = Column(Float)
    
    # Profit metrics
    gross_spread = Column(Float)
    estimated_fees = Column(Float)
    net_spread = Column(Float, index=True)
    net_spread_pct = Column(Float)
    
    # Cost of trade
    cost = Column(Float)
    
    # Profitability
    is_profitable = Column(Integer)  # Boolean stored as 0/1
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "event": self.event_description,
            "buy_yes_platform": self.buy_yes_platform,
            "buy_yes_price": self.buy_yes_price,
            "buy_no_platform": self.buy_no_platform,
            "buy_no_price": self.buy_no_price,
            "gross_spread": self.gross_spread,
            "estimated_fees": self.estimated_fees,
            "net_spread": self.net_spread,
            "net_spread_pct": self.net_spread_pct,
            "cost": self.cost,
            "is_profitable": bool(self.is_profitable),
        }


def init_db() -> None:
    """Initialize the database."""
    Base.metadata.create_all(bind=engine)
    logger.info(f"Database initialized at {DB_PATH}")


def get_db():
    """Dependency for FastAPI to get a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def save_signal(signal_data: dict) -> ArbitrageSignal:
    """Save an arbitrage opportunity to the database."""
    db = SessionLocal()
    try:
        signal = ArbitrageSignal(**signal_data)
        db.add(signal)
        db.commit()
        db.refresh(signal)
        logger.info(f"Signal saved: {signal.event_description} (spread: {signal.net_spread_pct:.2f}%)")
        return signal
    finally:
        db.close()


def get_recent_signals(limit: int = 100, profitable_only: bool = False) -> list[ArbitrageSignal]:
    """Fetch recent signals from the database."""
    db = SessionLocal()
    try:
        query = db.query(ArbitrageSignal).order_by(ArbitrageSignal.timestamp.desc())
        if profitable_only:
            query = query.filter(ArbitrageSignal.is_profitable == 1)
        return query.limit(limit).all()
    finally:
        db.close()


def get_signal_stats() -> dict:
    """Get statistics about stored signals."""
    db = SessionLocal()
    try:
        total = db.query(ArbitrageSignal).count()
        profitable = db.query(ArbitrageSignal).filter(ArbitrageSignal.is_profitable == 1).count()
        
        # Get best spread
        best_signal = db.query(ArbitrageSignal).order_by(
            ArbitrageSignal.net_spread_pct.desc()
        ).first()
        
        # Get stats by platform pair
        stats = db.query(
            ArbitrageSignal.buy_yes_platform,
            ArbitrageSignal.buy_no_platform,
            func.count(ArbitrageSignal.id).label("count"),
        ).group_by(
            ArbitrageSignal.buy_yes_platform,
            ArbitrageSignal.buy_no_platform,
        ).all()
        
        return {
            "total_signals": total,
            "profitable_signals": profitable,
            "best_spread": best_signal.net_spread_pct if best_signal else 0,
            "platform_pairs": [
                {
                    "buy_yes": s[0],
                    "buy_no": s[1],
                    "count": s[2],
                }
                for s in stats
            ],
        }
    finally:
        db.close()
