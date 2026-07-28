"""
LLM Engine — Gemma 4 31B as the PRIMARY structured decision-maker.

Uses Gemma 4 31B (1,500 RPD / 15 RPM / Unlimited TPM) for high-volume
structured financial analysis.  Falls back to Gemini 3.1 Flash Lite
(500 RPD) if the primary model errors out.

Receives:
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
import re
import time
import threading
from datetime import date
from typing import List, Dict, Optional
from google import genai
from pydantic import BaseModel, Field
from config import settings
from models import GeminiDecision

logger = logging.getLogger(__name__)

# ── Global Rate Limiter ─────────────────────────────────────────────────────
# Gemma 4 31B free tier = 15 RPM → 60s / 15 = 4s between calls.
# We use 4.2s (extra 0.2s margin) so we NEVER hit 429 in normal operation.
_rate_lock = threading.Lock()
_last_call_time = 0.0
_MIN_CALL_INTERVAL = 4.2  # 15 RPM = 4s minimum, +0.2s safety margin

# ── Daily Call Counter (for monitoring, NOT a hard limit) ────────────────────
_daily_counter_lock = threading.Lock()
_daily_calls = 0
_daily_calls_date = date.today()


def _increment_daily_counter() -> int:
    """Increment and return the daily call count. Resets at midnight."""
    global _daily_calls, _daily_calls_date
    with _daily_counter_lock:
        today = date.today()
        if today != _daily_calls_date:
            logger.info(f"Daily counter reset (was {_daily_calls} calls on {_daily_calls_date})")
            _daily_calls = 0
            _daily_calls_date = today
        _daily_calls += 1
        return _daily_calls


def get_daily_budget_status() -> dict:
    """Return current daily API usage stats for monitoring."""
    with _daily_counter_lock:
        today = date.today()
        calls = _daily_calls if today == _daily_calls_date else 0
    return {
        "calls_today": calls,
        "daily_limit": 1500,  # Gemma 4 31B free tier RPD
        "remaining": max(0, 1500 - calls),
        "model": settings.gemini_model,
        "date": str(today),
    }


def _rate_limit_wait():
    """Block until enough time has passed since the last API call."""
    global _last_call_time
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_call_time
        if elapsed < _MIN_CALL_INTERVAL:
            wait = _MIN_CALL_INTERVAL - elapsed
            logger.debug(f"Rate limiter: waiting {wait:.1f}s before next API call")
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
    """Synchronous API call with primary model + fallback cascade."""
    client = _get_client()
    if not client:
        logger.warning("API key not configured. Marking analysis FAILED (no LLM = no signal).")
        return {
            "status": "FAILED",
            "action": "HOLD",
            "confidence": None,
            "position_size_pct": 0.0,
            "risk_factors": ["LLM API key not configured"],
            "reasoning": "LLM unavailable (no API key). Ticker skipped this cycle.",
            "news_impact_score": None,
            "crisis_detected": False,
        }

    prompt = _build_analysis_prompt(
        ticker, technical_snapshot, macro_news,
        sector_news, stock_news, portfolio_state, risk_info,
    )

    # Try primary model (Gemma 4 31B), then fallback (Gemini 3.1 Flash Lite)
    models_to_try = [settings.gemini_model]
    if settings.gemini_fallback_model:
        models_to_try.append(settings.gemini_fallback_model)

    for model_name in models_to_try:
        result = _try_model(client, model_name, prompt, ticker)
        if result is not None:
            return result

    # All models failed
    logger.error(f"All models exhausted for {ticker}")
    return {
        "status": "FAILED",
        "action": "HOLD",
        "confidence": None,
        "position_size_pct": 0.0,
        "risk_factors": ["All LLM models failed after retries"],
        "reasoning": "LLM API unavailable after retries. Ticker skipped this cycle.",
        "news_impact_score": None,
        "crisis_detected": False,
    }



def _sanitize_json(raw: str) -> str:
    """Strip markdown code fences and trailing garbage from LLM JSON output.

    Gemma 4 31B occasionally wraps its JSON in ```json blocks or appends
    explanatory text after the closing brace, which breaks Pydantic parsing.
    """
    text = raw.strip()
    # Remove ```json ... ``` wrappers
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    # Find the outermost JSON object { ... }
    start = text.find("{")
    if start == -1:
        return text  # no JSON object found, return as-is for error handling
    depth = 0
    end = start
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    return text[start:end + 1]


def _try_model(client, model_name: str, prompt: str, ticker: str) -> Optional[dict]:
    """Try a specific model with retries. Returns parsed result or None."""
    max_retries = 3
    is_primary = (model_name == settings.gemini_model)

    for attempt in range(max_retries):
        try:
            _rate_limit_wait()
            count = _increment_daily_counter()

            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=GeminiAnalysisResponse,
                    temperature=0.15,
                ),
            )
            raw_text = response.text
            clean_json = _sanitize_json(raw_text)
            result = GeminiAnalysisResponse.model_validate_json(clean_json)
            logger.info(
                f"LLM analysis succeeded for {ticker} | "
                f"model={model_name} attempt={attempt+1} | "
                f"daily_calls={count}/1500"
            )

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

        except Exception as retry_err:
            err_str = str(retry_err)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                wait_time = 6 * (2 ** attempt)  # 6s, 12s, 24s
                logger.warning(
                    f"Rate limited on {model_name} for {ticker} "
                    f"(attempt {attempt+1}/{max_retries}), waiting {wait_time}s..."
                )
                time.sleep(wait_time)
            else:
                logger.warning(
                    f"Model {model_name} error for {ticker} "
                    f"(attempt {attempt+1}): {err_str[:150]}"
                )
                if not is_primary:
                    return None  # Don't waste retries on fallback for non-429 errors
                break  # Try fallback model

    # This model failed all retries
    label = "Primary" if is_primary else "Fallback"
    logger.warning(f"{label} model {model_name} failed for {ticker} after {max_retries} attempts")
    return None




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
    Full LLM structured analysis — the PRIMARY decision-maker.

    Uses Gemma 4 31B (primary) with Gemini 3.1 Flash Lite fallback.
    Returns a decision dict with action, confidence,
    position sizing, risk factors, and reasoning.
    """
    budget = get_daily_budget_status()
    logger.info(
        f"Running LLM analysis for {ticker} | "
        f"budget: {budget['calls_today']}/{budget['daily_limit']} used"
    )
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
