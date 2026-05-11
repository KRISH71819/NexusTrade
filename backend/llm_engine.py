"""
LLM Engine — Gemini as the PRIMARY structured decision-maker.

Instead of simple sentiment scoring, Gemini now receives:
  1. Macro news (global + India economy)
  2. Sector news
  3. Stock-specific news
  4. Full technical snapshot (RSI, MACD, BB, SMA crossovers)
  5. Current portfolio state
  6. Risk limits

And returns a structured JSON decision with action, confidence,
position sizing, risk factors, and detailed reasoning.
"""

import logging
import asyncio
import time
import threading
import random
from typing import List, Dict, Optional
from google import genai
from pydantic import BaseModel, Field
from config import settings
from models import GeminiDecision

logger = logging.getLogger(__name__)

# ── Global Rate Limiter ─────────────────────────────────────────────────────
# Enforces a minimum interval between consecutive Gemini API calls
# to stay safely under the free-tier RPM limit (15 RPM for pro, 30 for flash).
_rate_lock = threading.Lock()
_last_call_time = 0.0
_MIN_CALL_INTERVAL = 4.0  # seconds between calls (safe for 15 RPM)


def _rate_limit_wait():
    """Block until enough time has passed since the last Gemini API call."""
    global _last_call_time
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_call_time
        if elapsed < _MIN_CALL_INTERVAL:
            wait = _MIN_CALL_INTERVAL - elapsed
            logger.debug(f"Rate limiter: waiting {wait:.1f}s before next Gemini call")
            time.sleep(wait)
        _last_call_time = time.monotonic()


class GeminiAnalysisResponse(BaseModel):
    """Schema for Gemini structured output."""
    action: str = Field(description="Trading action: BUY, SELL, or HOLD")
    confidence: float = Field(description="Confidence in the decision, 0.0 to 1.0")
    position_size_pct: float = Field(description="Recommended position size as fraction of portfolio, 0.0 to 0.20")
    risk_factors: List[str] = Field(description="List of identified risk factors")
    reasoning: str = Field(description="Detailed 3-5 sentence explanation of the decision")
    news_impact_score: float = Field(description="Overall news impact on this stock, -1.0 (very bearish) to 1.0 (very bullish)")
    crisis_detected: bool = Field(description="True if any crisis-level event is detected that warrants immediate action")


# Lazy init client
_client = None


def _get_client():
    global _client
    if _client is None and settings.gemini_api_key:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def _build_analysis_prompt(
    ticker: str,
    technical_snapshot: Dict,
    macro_news: List[str],
    sector_news: List[str],
    stock_news: List[str],
    portfolio_state: Dict,
    risk_info: Dict,
) -> str:
    """Build a comprehensive prompt for Gemini structured analysis."""

    # Format technical indicators
    tech_lines = []
    for key, value in technical_snapshot.items():
        if isinstance(value, (int, float)):
            tech_lines.append(f"  {key}: {value:.4f}" if isinstance(value, float) else f"  {key}: {value}")

    # Format portfolio state
    cash = portfolio_state.get("cash", 0)
    total_value = portfolio_state.get("total_value", 0)
    holdings_count = len(portfolio_state.get("holdings", []))
    current_holding = None
    for h in portfolio_state.get("holdings", []):
        if h.get("ticker") == ticker:
            current_holding = h
            break

    holding_info = "Not currently held."
    if current_holding:
        qty = current_holding.get("quantity", 0)
        avg = current_holding.get("avg_price", 0)
        holding_info = f"Currently holding {qty} shares at avg Rs.{avg:.2f}"

    prompt = f"""You are a senior quantitative analyst at a hedge fund. Analyze the following data for {ticker} (NSE India) and make a trading decision.

═══ MACRO & GLOBAL NEWS (affects entire market) ═══
{chr(10).join(f'• {h}' for h in macro_news[:8]) if macro_news else '• No significant macro news available'}

═══ SECTOR NEWS ═══
{chr(10).join(f'• {h}' for h in sector_news[:5]) if sector_news else '• No sector-specific news available'}

═══ STOCK-SPECIFIC NEWS ({ticker}) ═══
{chr(10).join(f'• {h}' for h in stock_news[:5]) if stock_news else '• No stock-specific news available'}

═══ TECHNICAL INDICATORS ═══
{chr(10).join(tech_lines) if tech_lines else '  No technical data available'}

═══ PORTFOLIO STATE ═══
  Cash available: Rs.{cash:,.2f}
  Total portfolio value: Rs.{total_value:,.2f}
  Open positions: {holdings_count}
  {ticker} status: {holding_info}

═══ RISK LIMITS ═══
  Max position size: {settings.max_position_pct*100:.0f}% of portfolio
  Stop-loss threshold: {settings.stop_loss_pct*100:.0f}% below entry
  Max sector concentration: {settings.max_sector_stocks} stocks per sector
  Portfolio drawdown limit: {settings.max_drawdown_pct*100:.0f}%
  Sector: {risk_info.get('sector', 'Unknown')}
  Sector stocks already held: {risk_info.get('sector_exposure_count', 0)}/{settings.max_sector_stocks}

═══ DECISION RULES ═══
1. If crisis-level events are detected (war, market crash, pandemic), set crisis_detected=true
2. If crisis_detected is true and we hold the stock, recommend SELL
3. If crisis_detected is true and we don't hold it, recommend HOLD (don't buy into crisis)
4. For BUY: require strong technical AND positive news alignment
5. For SELL: technical weakness OR negative news OR risk limits exceeded
6. Position size should be proportional to your confidence (high confidence = larger position)
7. Consider macro news as a market-wide sentiment override — if macro is very bearish, avoid BUY even if stock technicals look good
8. Be conservative — when in doubt, HOLD

Analyze all data and return your structured trading decision."""

    return prompt


def _analyze_sync(
    ticker: str,
    technical_snapshot: Dict,
    macro_news: List[str],
    sector_news: List[str],
    stock_news: List[str],
    portfolio_state: Dict,
    risk_info: Dict,
) -> dict:
    """Synchronous Gemini API call for structured analysis."""
    client = _get_client()
    if not client:
        logger.warning("Gemini API key not configured. Returning neutral decision.")
        return GeminiDecision().model_dump()

    prompt = _build_analysis_prompt(
        ticker, technical_snapshot, macro_news,
        sector_news, stock_news, portfolio_state, risk_info,
    )

    try:
        max_retries = 5
        result = None

        for attempt in range(max_retries):
            try:
                # Enforce global rate limit before every API call
                _rate_limit_wait()

                response = client.models.generate_content(
                    model=settings.gemini_model,
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=GeminiAnalysisResponse,
                        temperature=0.15,  # low temperature for consistent decisions
                    ),
                )
                result = GeminiAnalysisResponse.model_validate_json(response.text)
                logger.info(f"Gemini analysis succeeded for {ticker} (attempt {attempt+1})")
                break  # success

            except Exception as retry_err:
                err_str = str(retry_err)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    # Exponential backoff with jitter: 15-20s, 30-40s, 60-80s, 120-160s
                    base_wait = 15 * (2 ** attempt)
                    jitter = random.uniform(0, base_wait * 0.3)
                    wait_time = base_wait + jitter
                    logger.warning(
                        f"Gemini rate limited for {ticker} (attempt {attempt+1}/{max_retries}), "
                        f"waiting {wait_time:.0f}s..."
                    )
                    time.sleep(wait_time)
                else:
                    raise retry_err  # non-rate-limit error, don't retry

        if result is None:
            logger.error(f"Gemini exhausted all retries for {ticker}")
            return {
                "action": "HOLD",
                "confidence": 0.5,
                "position_size_pct": 0.0,
                "risk_factors": ["Gemini rate limited after retries"],
                "reasoning": "Gemini API rate limited. Defaulting to HOLD.",
                "news_impact_score": 0.0,
                "crisis_detected": False,
            }

        # Clamp values to valid ranges
        confidence = max(0.0, min(1.0, result.confidence))
        position_size = max(0.0, min(settings.max_position_pct, result.position_size_pct))
        news_impact = max(-1.0, min(1.0, result.news_impact_score))

        return {
            "action": result.action.upper() if result.action else "HOLD",
            "confidence": confidence,
            "position_size_pct": position_size,
            "risk_factors": result.risk_factors or [],
            "reasoning": result.reasoning or "",
            "news_impact_score": news_impact,
            "crisis_detected": result.crisis_detected,
        }

    except Exception as e:
        logger.error(f"Gemini API error for {ticker}: {e}")
        return {
            "action": "HOLD",
            "confidence": 0.5,
            "position_size_pct": 0.0,
            "risk_factors": [f"Gemini API error: {str(e)[:100]}"],
            "reasoning": f"Unable to analyze due to API error: {str(e)[:200]}",
            "news_impact_score": 0.0,
            "crisis_detected": False,
        }


async def analyze_with_gemini(
    ticker: str,
    technical_snapshot: Dict,
    macro_news: List[str],
    sector_news: List[str],
    stock_news: List[str],
    portfolio_state: Dict,
    risk_info: Dict,
) -> dict:
    """
    Full Gemini structured analysis — the PRIMARY decision-maker.

    Returns a GeminiDecision dict with action, confidence,
    position sizing, risk factors, and reasoning.
    """
    logger.info(f"Running Gemini structured analysis for {ticker}")
    return await asyncio.to_thread(
        _analyze_sync,
        ticker, technical_snapshot, macro_news,
        sector_news, stock_news, portfolio_state, risk_info,
    )


# ── Legacy compatibility wrapper ─────────────────────────────────────────────

async def analyze_sentiment(ticker: str, headlines: List[str]) -> dict:
    """
    Legacy wrapper — kept for backward compatibility.
    Now delegates to the full Gemini analysis with minimal context.
    """
    result = await analyze_with_gemini(
        ticker=ticker,
        technical_snapshot={},
        macro_news=[],
        sector_news=[],
        stock_news=headlines,
        portfolio_state={"cash": 0, "total_value": 0, "holdings": []},
        risk_info={},
    )
    return {
        "sentiment_score": result["news_impact_score"],
        "explanation": result["reasoning"],
    }
