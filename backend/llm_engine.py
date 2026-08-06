"""
LLM Engine — Multi-Agent Chain Orchestrator.

Chain mode (recommended):
  Agent 2 (Analyst):  Groq API — groq/compound first, llama-3.3-70b fallback
  Agent 3 (Reviewer): Gemma 4 31B (challenges Groq’s decisions)
  Fallback:           If Groq is down → Gemma auto-promotes to analyst

Single mode:
  Gemma 4 31B only

Entry point: analyze_with_llm()  (replaces analyze_with_gemini in scheduler)
"""

import logging
import asyncio
import json as json_module
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




async def analyze_with_gemma(
    ticker: str,
    technical_snapshot: Dict,
    macro_news: List[str],
    sector_news: List[str],
    stock_news: List[str],
    portfolio_state: Dict,
    risk_info: Dict,
) -> dict:
    """
    Gemma 4 analysis — FALLBACK analyst when Kimi is down, also used as reviewer base.

    Uses Gemma 4 31B (primary) with Gemini 3.1 Flash Lite fallback.
    Returns a decision dict with action, confidence,
    position sizing, risk factors, and reasoning.
    """
    budget = get_daily_budget_status()
    logger.info(
        f"Running Gemma analysis for {ticker} | "
        f"budget: {budget['calls_today']}/{budget['daily_limit']} used"
    )
    result = await asyncio.to_thread(
        _analyze_sync,
        ticker, technical_snapshot, macro_news,
        sector_news, stock_news, portfolio_state, risk_info,
    )
    if result.get("status") != "FAILED":
        result["analyst_model"] = "gemma"
    return result


# Backward-compatible alias (scheduler may still import this name)
analyze_with_gemini = analyze_with_gemma


# ═══════════════════════════════════════════════════════════════════════════════
#   GEMMA REVIEWER — challenges the primary analyst's (Kimi K3) decisions
# ═══════════════════════════════════════════════════════════════════════════════

_REVIEWER_PROMPT = """You are a risk-focused portfolio manager reviewing a trade recommendation from a quantitative analyst.

═══ ANALYST'S RECOMMENDATION ═══
  Stock: {ticker}
  Action: {action}
  Confidence: {confidence:.0%}
  Reasoning: {reasoning}
  Risk factors identified: {risk_factors}

═══ KEY DATA (verify the analyst's claims) ═══
  Price: Rs.{price}
  RSI: {rsi}
  MACD signal: {macd_signal}
  Volume: {vol_ratio}x avg
  News sentiment: {news_impact}
  Market regime: {regime}
  Portfolio cash: {cash_pct}

═══ YOUR TASK ═══
The analyst may be overconfident. Your job:
1. Does the reasoning hold up against the actual data?
2. What risks did the analyst MISS or underweight?
3. Is the confidence level justified?
4. Your verdict: AGREE (sounds right), CAUTION (lower confidence), or VETO (block trade entirely)

Return ONLY valid JSON with no extra text:
{{
  "verdict": "AGREE" or "CAUTION" or "VETO",
  "adjusted_confidence": 0.0 to 1.0,
  "missed_risks": ["risk 1", "risk 2"],
  "review_notes": "your reasoning"
}}"""


def _gemma_review_sync(
    ticker: str,
    analyst_result: dict,
    raw_context: dict,
) -> Optional[dict]:
    """
    Synchronous Gemma review call.
    Returns parsed review dict or None on failure.
    """
    client = _get_client()
    if not client:
        return None

    price = raw_context.get("price", 0)
    price_str = f"{price:.2f}" if isinstance(price, (int, float)) else str(price)

    news_impact = analyst_result.get("news_impact_score", 0)
    news_str = f"{news_impact:+.2f}" if isinstance(news_impact, (int, float)) else str(news_impact)

    cash_pct = raw_context.get("cash_pct", 0)
    cash_str = f"{cash_pct:.0%}" if isinstance(cash_pct, (int, float)) else str(cash_pct)

    # Build reviewer prompt with analyst output + key data
    prompt = _REVIEWER_PROMPT.format(
        ticker=ticker,
        action=analyst_result.get("action", "HOLD"),
        confidence=analyst_result.get("confidence", 0.5),
        reasoning=analyst_result.get("reasoning", "No reasoning provided")[:500],
        risk_factors=", ".join(analyst_result.get("risk_factors", [])[:5]) or "None identified",
        price=price_str,
        rsi=raw_context.get("rsi", "N/A"),
        macd_signal=raw_context.get("macd_signal", "N/A"),
        vol_ratio=raw_context.get("volume_ratio", "N/A"),
        news_impact=news_str,
        regime=raw_context.get("market_regime", "UNKNOWN"),
        cash_pct=cash_str,
    )

    try:
        _rate_limit_wait()
        _increment_daily_counter()

        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=settings.gemini_reviewer_temperature,
            ),
        )

        raw_text = response.text
        clean = _sanitize_json(raw_text)
        parsed = json_module.loads(clean)

        verdict = str(parsed.get("verdict", "AGREE")).upper()
        if verdict not in ("AGREE", "CAUTION", "VETO"):
            verdict = "AGREE"

        adj_conf = parsed.get("adjusted_confidence")
        if adj_conf is not None:
            adj_conf = max(0.0, min(1.0, float(adj_conf)))

        logger.info(
            f"Gemma review for {ticker}: verdict={verdict}, "
            f"adj_conf={adj_conf}, missed_risks={parsed.get('missed_risks', [])}"
        )

        return {
            "verdict": verdict,
            "adjusted_confidence": adj_conf,
            "missed_risks": parsed.get("missed_risks", []),
            "review_notes": parsed.get("review_notes", ""),
        }

    except Exception as e:
        logger.warning(f"Gemma review failed for {ticker}: {e}")
        return None


async def review_with_gemma(
    ticker: str,
    analyst_result: dict,
    raw_context: dict,
) -> Optional[dict]:
    """Async wrapper for Gemma reviewer."""
    return await asyncio.to_thread(
        _gemma_review_sync, ticker, analyst_result, raw_context,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#   REVIEW VERDICT ARBITER (code, not LLM)
# ═══════════════════════════════════════════════════════════════════════════════

# Track review stats for daily reporting
_review_stats_lock = threading.Lock()
_review_stats = {"agreed": 0, "cautioned": 0, "vetoed": 0, "skipped": 0, "date": str(date.today())}


def get_review_stats() -> dict:
    """Return today's review verdict stats."""
    with _review_stats_lock:
        today = str(date.today())
        if _review_stats["date"] != today:
            _review_stats.update({"agreed": 0, "cautioned": 0, "vetoed": 0, "skipped": 0, "date": today})
        return dict(_review_stats)


def _record_review_stat(verdict: str):
    """Record a review verdict for daily stats."""
    with _review_stats_lock:
        today = str(date.today())
        if _review_stats["date"] != today:
            _review_stats.update({"agreed": 0, "cautioned": 0, "vetoed": 0, "skipped": 0, "date": today})
        key = {"AGREE": "agreed", "CAUTION": "cautioned", "VETO": "vetoed"}.get(verdict, "skipped")
        _review_stats[key] = _review_stats.get(key, 0) + 1


def _apply_review_verdict(analyst_result: dict, review: dict) -> dict:
    """
    Code-based arbiter. Applies reviewer verdict to analyst result.

    AGREE:   boost confidence by chain_agree_boost (+8%)
    CAUTION: use reviewer's adjusted_confidence (floored at chain_caution_floor)
    VETO:    override action to HOLD, confidence = 0.30
    """
    verdict = review.get("verdict", "AGREE").upper()
    result = {**analyst_result}
    original_conf = analyst_result.get("confidence", 0.5)

    if verdict == "AGREE":
        result["confidence"] = min(1.0, original_conf + settings.chain_agree_boost)
    elif verdict == "CAUTION":
        adj = review.get("adjusted_confidence")
        if adj is not None:
            result["confidence"] = max(settings.chain_caution_floor, adj)
        else:
            result["confidence"] = max(settings.chain_caution_floor, original_conf * 0.8)
    elif verdict == "VETO":
        result["action"] = "HOLD"
        result["confidence"] = 0.30

    _record_review_stat(verdict)

    result["review"] = {
        "verdict": verdict,
        "original_confidence": original_conf,
        "adjusted_confidence": result["confidence"],
        "missed_risks": review.get("missed_risks", []),
        "notes": review.get("review_notes", ""),
        "reviewer_model": settings.gemini_model,
    }
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#   MAIN ENTRY POINT — analyze_with_llm (replaces analyze_with_gemini)
# ═══════════════════════════════════════════════════════════════════════════════

async def analyze_with_llm(
    ticker: str,
    technical_snapshot: Dict,
    macro_news: List[str],
    sector_news: List[str],
    stock_news: List[str],
    portfolio_state: Dict,
    risk_info: Dict,
    raw_context: Optional[Dict] = None,
) -> dict:
    """
    Multi-agent LLM analysis — the MAIN entry point.

    Chain mode:  Groq analyst (compound→llama fallback) → Gemma 4 reviewer
    Single mode: Gemma 4 only

    Falls back gracefully if Groq is down.
    """
    analyst_result = None
    analyst_model = "groq"

    # ── Step 1: Try Groq as primary analyst ─────────────────────────────
    if settings.llm_mode == "chain" and settings.groq_api_key:
        try:
            from groq_engine import analyze_with_groq
            analyst_result = await asyncio.wait_for(
                analyze_with_groq(
                    ticker, technical_snapshot, macro_news,
                    sector_news, stock_news, portfolio_state, risk_info,
                ),
                timeout=settings.groq_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Groq timed out for {ticker} ({settings.groq_timeout}s). Falling back to Gemma.")
        except Exception as e:
            logger.warning(f"Groq error for {ticker}: {e}. Falling back to Gemma.")

    # ── Step 2: Fallback to Gemma if Groq failed ──────────────────────
    if analyst_result is None:
        analyst_result = await analyze_with_gemma(
            ticker, technical_snapshot, macro_news,
            sector_news, stock_news, portfolio_state, risk_info,
        )
        analyst_model = "gemma"

        # If Gemma also failed, return the FAILED result
        if analyst_result.get("status") == "FAILED":
            analyst_result["review"] = None
            return analyst_result

    # ── Step 3: Gemma reviews Groq’s decision (chain mode only) ───────
    # Only review if: (a) Groq was the analyst, (b) action is BUY/SELL
    if analyst_model == "groq" and analyst_result.get("action") in ("BUY", "SELL"):
        review = None
        try:
            review = await review_with_gemma(
                ticker, analyst_result, raw_context or {},
            )
        except Exception as e:
            logger.warning(f"Reviewer error for {ticker}: {e}. Using analyst-only decision.")

        if review:
            analyst_result = _apply_review_verdict(analyst_result, review)
            verdict = review.get("verdict", "?")
            logger.info(
                f"[GROQ→GEMMA:{verdict}] {ticker} "
                f"{analyst_result.get('action')} "
                f"{analyst_result['review']['original_confidence']:.2f}"
                f"→{analyst_result['confidence']:.2f}"
            )
        else:
            _record_review_stat("SKIPPED")
            analyst_result["review"] = {"skipped": True, "reason": "reviewer unavailable"}
            logger.info(f"[GROQ-ONLY] {ticker} — reviewer unavailable, using analyst-only decision")
    elif analyst_model == "groq":
        # Groq said HOLD → skip review
        _record_review_stat("SKIPPED")
        analyst_result["review"] = {"skipped": True, "reason": "HOLD — no review needed"}
    else:
        # Gemma was analyst (fallback) → no review possible
        analyst_result["review"] = None
        if settings.llm_mode == "chain" and settings.groq_api_key:
            logger.info(f"[GEMMA-ONLY] {ticker} — Groq was down, Gemma auto-promoted to analyst")

    analyst_result["analyst_model"] = analyst_model
    return analyst_result


# ── Legacy compatibility wrapper ─────────────────────────────────────────────

async def analyze_sentiment(ticker: str, headlines: List[str]) -> dict:
    """
    Legacy wrapper — kept for backward compatibility.
    Now delegates to the full analysis with minimal context.
    """
    result = await analyze_with_llm(
        ticker=ticker,
        technical_snapshot={},
        macro_news=[],
        sector_news=[],
        stock_news=headlines,
        portfolio_state={"cash": 0, "total_value": 0, "holdings": []},
        risk_info={},
    )
    return {
        "sentiment_score": result.get("news_impact_score"),
        "explanation": result.get("reasoning", ""),
    }
