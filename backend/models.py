"""
Pydantic models for all domain entities.
"""

from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from enum import Enum


class TradeAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


# ── News ─────────────────────────────────────────────────────────────────────

class NewsItem(BaseModel):
    headline: str
    source: str = ""
    url: str = ""
    published_at: Optional[datetime] = None


# ── Holdings ─────────────────────────────────────────────────────────────────

class Holding(BaseModel):
    ticker: str
    quantity: int
    avg_price: float
    current_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0


# ── Portfolio ────────────────────────────────────────────────────────────────

class Portfolio(BaseModel):
    cash: float = 1_000_000.0
    holdings: List[Holding] = []
    total_value: float = 1_000_000.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PortfolioSnapshot(BaseModel):
    timestamp: datetime
    cash: float
    holdings_value: float
    total_value: float


# ── Analysis ─────────────────────────────────────────────────────────────────

class AnalysisResult(BaseModel):
    ticker: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    current_price: float

    # ML Engine output
    ml_confidence: float  # 0.0 – 1.0 (probability of bullish trend)
    ml_features_used: dict = {}

    # LLM Engine output
    news_headlines: List[str] = []  # exactly 3 headlines
    gemini_sentiment_score: float  # -1.0 to 1.0
    gemini_explanation: str = ""  # 2-sentence reasoning

    # Decision
    action: TradeAction = TradeAction.HOLD
    action_reason: str = ""


# ── Trade ────────────────────────────────────────────────────────────────────

class Trade(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    ticker: str
    action: TradeAction
    quantity: int
    price: float
    total_value: float

    # Transparency — full AI brain snapshot
    ml_confidence: float
    news_headlines: List[str] = []
    gemini_sentiment_score: float
    gemini_explanation: str

    # Portfolio state after trade
    portfolio_snapshot: PortfolioSnapshot


# ── Market Data ──────────────────────────────────────────────────────────────

class OHLCVBar(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class MarketDataResponse(BaseModel):
    ticker: str
    bars: List[OHLCVBar] = []
    indicators: dict = {}  # SMA, EMA, RSI, MACD etc.
    last_updated: Optional[datetime] = None


# ── API Responses ────────────────────────────────────────────────────────────

class TradeResponse(BaseModel):
    trades: List[Trade] = []
    total_count: int = 0


class AnalysisLogResponse(BaseModel):
    analyses: List[AnalysisResult] = []
    total_count: int = 0
