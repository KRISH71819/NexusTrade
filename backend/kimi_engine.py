"""
Kimi K3 Analyst Engine — PRIMARY analyst powered by Moonshot AI's Kimi K3 (2.8T params).

Uses the OpenAI-compatible API via TokenRouter (FREE tier).
Receives the same comprehensive data as Gemma and returns an identical
structured decision dict, so the scoring pipeline doesn't care which
model produced the analysis.

If Kimi is unavailable (timeout, error, bad response), returns None —
the caller (llm_engine.py) auto-promotes Gemma to analyst.
"""

import logging
import asyncio
import json
import re
import time
import threading
from datetime import date
from typing import Dict, List, Optional

from config import settings

logger = logging.getLogger(__name__)

# ── Rate Limiter (separate from Gemma) ────────────────────────────────────
# TokenRouter free tier has limited concurrency — space calls 2s apart.
_rate_lock = threading.Lock()
_last_call_time = 0.0
_MIN_CALL_INTERVAL = 2.0  # seconds between calls

# ── Daily Call Counter ─────────────────────────────────────────────────────
_daily_counter_lock = threading.Lock()
_daily_calls = 0
_daily_calls_date = date.today()


def _increment_daily_counter() -> int:
    """Increment and return the daily call count. Resets at midnight."""
    global _daily_calls, _daily_calls_date
    with _daily_counter_lock:
        today = date.today()
        if today != _daily_calls_date:
            logger.info(f"Kimi daily counter reset (was {_daily_calls} on {_daily_calls_date})")
            _daily_calls = 0
            _daily_calls_date = today
        _daily_calls += 1
        return _daily_calls


def get_kimi_daily_usage() -> dict:
    """Return current daily Kimi API usage stats for monitoring."""
    with _daily_counter_lock:
        today = date.today()
        calls = _daily_calls if today == _daily_calls_date else 0
    return {
        "calls_today": calls,
        "model": settings.kimi_model,
        "date": str(today),
    }



def _rate_limit_wait():
    """Block until enough time has passed since the last Kimi API call."""
    global _last_call_time
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_call_time
        if elapsed < _MIN_CALL_INTERVAL:
            wait = _MIN_CALL_INTERVAL - elapsed
            logger.debug(f"Kimi rate limiter: waiting {wait:.1f}s")
            time.sleep(wait)
        _last_call_time = time.monotonic()


# ── Lazy OpenAI client ─────────────────────────────────────────────────────
_client = None


def _get_client():
    """Lazy-init the OpenAI client pointed at TokenRouter."""
    global _client
    if _client is None and settings.kimi_api_key:
        from openai import OpenAI
        _client = OpenAI(
            api_key=settings.kimi_api_key,
            base_url=settings.kimi_base_url,
        )
    return _client


# ── JSON Sanitizer ─────────────────────────────────────────────────────────

def _sanitize_json(raw: str) -> str:
    """Strip markdown code fences and trailing garbage from LLM JSON output."""
    text = raw.strip()
    # Remove ```json ... ``` wrappers
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    # Find the outermost JSON object { ... }
    start = text.find("{")
    if start == -1:
        return text
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
    fragment = text[start:end + 1]
    # Python-dict style output: quote unquoted keys (e.g. {action: "BUY"} -> {"action": "BUY"})
    fragment = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', fragment)
    # Normalize single-quoted strings to double-quoted (safe: JSON has no escapes in our schema)
    fragment = re.sub(r"'([^'\\]*)'", r'"\1"', fragment)
    return fragment


# ── Analyst Prompt (same data as Gemma gets) ───────────────────────────────

def _build_kimi_analyst_prompt(
    ticker: str,
    technical_snapshot: Dict,
    macro_news: List[str],
    sector_news: List[str],
    stock_news: List[str],
    portfolio_state: Dict,
    risk_info: Dict,
) -> str:
    """Build the comprehensive analyst prompt for Kimi K3."""

    # Format technical indicators
    tech_lines = []
    for key, value in technical_snapshot.items():
        if isinstance(value, (int, float)):
            tech_lines.append(
                f"  {key}: {value:.4f}" if isinstance(value, float) else f"  {key}: {value}"
            )

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
  Max position size: {settings.max_single_trade_pct*100:.0f}% of portfolio
  Stop-loss threshold: {settings.stop_loss_pct*100:.0f}% below entry
  Max sector concentration: {settings.max_sector_value_pct*100:.0f}% portfolio value per sector
  Daily loss halt threshold: {settings.daily_loss_halt_pct*100:.1f}%
  Sector: {risk_info.get('sector', 'Unknown')}
  Sector stocks already held: {risk_info.get('sector_exposure_count', 0)}

═══ DECISION RULES ═══
1. If crisis-level events are detected (war, market crash, pandemic), set crisis_detected=true
2. If crisis_detected is true and we hold the stock, recommend SELL
3. If crisis_detected is true and we don't hold it, recommend HOLD (don't buy into crisis)
4. For BUY: require strong technical AND positive news alignment
5. For SELL: technical weakness OR negative news OR risk limits exceeded
6. Position size should be proportional to your confidence (high confidence = larger position)
7. Consider macro news as a market-wide sentiment override — if macro is very bearish, avoid BUY even if stock technicals look good
8. Be conservative — when in doubt, HOLD

Return ONLY valid JSON with these exact fields.
IMPORTANT: Use strict JSON — ALL keys and string values must be in DOUBLE quotes. Output nothing before or after the JSON object.
{{
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": 0.0 to 1.0,
  "position_size_pct": 0.0 to 0.20,
  "risk_factors": ["factor1", "factor2"],
  "reasoning": "3-5 sentence explanation",
  "news_impact_score": -1.0 to 1.0,
  "crisis_detected": true | false
}}"""

    return prompt


# ── Synchronous API Call ───────────────────────────────────────────────────

def _kimi_analyze_sync(
    ticker: str,
    technical_snapshot: Dict,
    macro_news: List[str],
    sector_news: List[str],
    stock_news: List[str],
    portfolio_state: Dict,
    risk_info: Dict,
) -> Optional[dict]:
    """
    Synchronous Kimi K3 analysis call.

    Returns structured analysis dict or None on any failure.
    """
    client = _get_client()
    if not client:
        logger.warning("Kimi API key not configured.")
        return None

    prompt = _build_kimi_analyst_prompt(
        ticker, technical_snapshot, macro_news,
        sector_news, stock_news, portfolio_state, risk_info,
    )

    max_retries = 2  # fewer retries than Gemma — save time for fallback

    for attempt in range(max_retries):
        try:
            _rate_limit_wait()
            count = _increment_daily_counter()

            response = client.chat.completions.create(
                model=settings.kimi_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a senior quantitative analyst. "
                            "Always respond with strict JSON only: double-quoted keys and string values, "
                            "no markdown fences, no single quotes, no text outside the JSON object."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=settings.kimi_temperature,
                max_completion_tokens=1024,
            )

            raw_text = response.choices[0].message.content
            if not raw_text:
                logger.warning(f"Kimi returned empty response for {ticker} (attempt {attempt + 1})")
                continue

            clean_json = _sanitize_json(raw_text)
            parsed = json.loads(clean_json)

            # Validate required fields
            action = str(parsed.get("action", "HOLD")).upper()
            if action not in ("BUY", "SELL", "HOLD"):
                action = "HOLD"

            confidence = float(parsed.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))

            position_size = float(parsed.get("position_size_pct", 0.0))
            position_size = max(0.0, min(settings.max_single_trade_pct, position_size))

            news_impact = float(parsed.get("news_impact_score", 0.0))
            news_impact = max(-1.0, min(1.0, news_impact))

            logger.info(
                f"Kimi analysis succeeded for {ticker} | "
                f"model={settings.kimi_model} attempt={attempt + 1} | "
                f"daily_calls={count} | action={action} conf={confidence:.2f}"
            )

            return {
                "action": action,
                "confidence": confidence,
                "position_size_pct": position_size,
                "risk_factors": parsed.get("risk_factors", []),
                "reasoning": parsed.get("reasoning", ""),
                "news_impact_score": news_impact,
                "crisis_detected": bool(parsed.get("crisis_detected", False)),
                "analyst_model": "kimi",
            }

        except json.JSONDecodeError as e:
            logger.warning(
                f"Kimi JSON parse error for {ticker} (attempt {attempt + 1}): {e}"
            )
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "rate" in err_str.lower():
                wait_time = 5 * (2 ** attempt)
                logger.warning(
                    f"Kimi rate limited for {ticker} (attempt {attempt + 1}), "
                    f"waiting {wait_time}s..."
                )
                time.sleep(wait_time)
            else:
                logger.warning(
                    f"Kimi error for {ticker} (attempt {attempt + 1}): {err_str[:200]}"
                )
                break  # non-retryable error → fall back to Gemma

    logger.warning(f"Kimi K3 failed for {ticker} after {max_retries} attempts — falling back to Gemma")
    return None


# ── Async Wrapper ──────────────────────────────────────────────────────────

async def analyze_with_kimi(
    ticker: str,
    technical_snapshot: Dict,
    macro_news: List[str],
    sector_news: List[str],
    stock_news: List[str],
    portfolio_state: Dict,
    risk_info: Dict,
) -> Optional[dict]:
    """
    Primary LLM analyst — Kimi K3 (2.8T parameters).

    Returns structured analysis dict or None if unavailable.
    When None, the caller should fall back to Gemma as analyst.
    """
    logger.info(f"Running Kimi K3 analysis for {ticker}")
    return await asyncio.to_thread(
        _kimi_analyze_sync,
        ticker, technical_snapshot, macro_news,
        sector_news, stock_news, portfolio_state, risk_info,
    )
