"""
Pydantic models for all domain entities.
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime
from enum import Enum


class TradeAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class NewsLevel(str, Enum):
    MACRO = "MACRO"
    SECTOR = "SECTOR"
    STOCK = "STOCK"


class NewsSentiment(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    CRISIS = "CRISIS"


# ── News ─────────────────────────────────────────────────────────────────────

class NewsItem(BaseModel):
    headline: str
    source: str = ""
    url: str = ""
    published_at: Optional[datetime] = None
    level: NewsLevel = NewsLevel.STOCK
    sentiment: NewsSentiment = NewsSentiment.NEUTRAL
    impact_score: float = 0.0  # -1.0 to 1.0


class NewsIntelligence(BaseModel):
    """Aggregated news intelligence for a single analysis cycle."""
    macro_news: List[NewsItem] = []
    sector_news: List[NewsItem] = []
    stock_news: List[NewsItem] = []
    crisis_detected: bool = False
    crisis_reason: str = ""
    overall_news_score: float = 0.0  # -1.0 to 1.0
    fetched_at: datetime = Field(default_factory=lambda: datetime.utcnow())


# ── Risk Assessment ──────────────────────────────────────────────────────────

class RiskAssessment(BaseModel):
    """Risk evaluation for a position or potential trade."""
    stop_loss_price: Optional[float] = None
    trailing_stop_price: Optional[float] = None
    position_risk_score: float = 0.0  # 0.0 = safe, 1.0 = max risk
    sector: str = ""
    sector_exposure_count: int = 0
    portfolio_drawdown_pct: float = 0.0
    max_allowed_position_pct: float = 0.20
    risk_flags: List[str] = []
    risk_approved: bool = True


# ── Holdings ─────────────────────────────────────────────────────────────────

class Holding(BaseModel):
    ticker: str
    quantity: int
    avg_price: float
    current_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    peak_price: float = 0.0  # for trailing stop
    sector: str = ""


# ── Portfolio ────────────────────────────────────────────────────────────────

class Portfolio(BaseModel):
    cash: float = 1_000_000.0
    holdings: List[Holding] = []
    total_value: float = 1_000_000.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    peak_value: float = 1_000_000.0  # for drawdown calculation
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PortfolioSnapshot(BaseModel):
    timestamp: datetime
    cash: float
    holdings_value: float
    total_value: float


# ── Gemini Structured Decision ───────────────────────────────────────────────

class GeminiDecision(BaseModel):
    """Structured output from the Gemini analyst."""
    action: str = "HOLD"  # BUY, SELL, HOLD
    confidence: float = 0.5  # 0.0 - 1.0
    position_size_pct: float = 0.0  # 0.0 - 0.20
    risk_factors: List[str] = []
    reasoning: str = ""
    news_impact_score: float = 0.0  # -1.0 to 1.0
    crisis_detected: bool = False


# ── Analysis ─────────────────────────────────────────────────────────────────

class AnalysisResult(BaseModel):
    ticker: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    current_price: float

    # ML Engine output
    ml_confidence: Optional[float] = None  # 0.0-1.0 bullish probability; None when ML FAILED (insufficient data / error)
    ml_features_used: dict = {}

    # LLM Engine output (Gemini structured decision)
    news_headlines: List[str] = []
    gemini_sentiment_score: float  # -1.0 to 1.0
    gemini_explanation: str = ""
    gemini_confidence: float = 0.5
    gemini_risk_factors: List[str] = []
    gemini_position_size_pct: float = 0.0

    # News Intelligence
    news_intelligence: Optional[Dict] = None
    crisis_detected: bool = False

    # Risk Assessment
    risk_assessment: Optional[Dict] = None

    # Final Decision
    final_score: float = 0.0  # weighted composite score
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
    ml_confidence: Optional[float] = None
    news_headlines: List[str] = []
    gemini_sentiment_score: float
    gemini_explanation: str
    final_score: float = 0.0
    crisis_detected: bool = False

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
