"""
Groq Analyst Engine — PRIMARY analyst powered by Groq API.

Model chain (best → fallback):
  1. groq/compound     (RPD=250/day)  — Groq's compound system, highest quality
  2. llama-3.3-70b-versatile (RPD=1K/day) — solid 70B fallback if compound exhausted

Uses the OpenAI-compatible API via Groq (FREE tier).
Receives the same comprehensive data as Gemma and returns an identical
structured decision dict, so the scoring pipeline doesn't care which
model produced the analysis.

If Groq is unavailable (timeout, error, bad response), returns None —
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

# ── Rate Limiter ──────────────────────────────────────────────────────────────
# Groq free tier: 30 RPM. Space calls 2s apart to stay safely within limits.
_rate_lock = threading.Lock()
_last_call_time = 0.0
_MIN_CALL_INTERVAL = 2.0  # seconds between calls

# ── Daily Call Counter per model ──────────────────────────────────────────────
# compound: 250 RPD limit  |  llama-3.3-70b-versatile: 1000 RPD limit
_daily_counter_lock = threading.Lock()
_daily_calls: Dict[str, int] = {"compound": 0, "llama": 0}
_daily_calls_date = date.today()

# Model RPD limits (from Groq console)
_MODEL_RPD_LIMITS = {
    "groq/compound": 250,
    "llama-3.3-70b-versatile": 1000,
}


def _reset_daily_counters_if_needed():
    """Reset daily counters at midnight."""
    global _daily_calls, _daily_calls_date
    today = date.today()
    if today != _daily_calls_date:
        logger.info(
            f"Groq daily counters reset (compound={_daily_calls['compound']}, "
            f"llama={_daily_calls['llama']} on {_daily_calls_date})"
        )
        _daily_calls = {"compound": 0, "llama": 0}
        _daily_calls_date = today


def _increment_counter(model_key: str) -> int:
    """Increment and return the daily call count for the given model key."""
    global _daily_calls
    with _daily_counter_lock:
        _reset_daily_counters_if_needed()
        _daily_calls[model_key] = _daily_calls.get(model_key, 0) + 1
        return _daily_calls[model_key]


def _is_model_exhausted(groq_model: str) -> bool:
    """Return True if the model has hit its daily request limit."""
    with _daily_counter_lock:
        _reset_daily_counters_if_needed()
        limit = _MODEL_RPD_LIMITS.get(groq_model, 9999)
        model_key = "compound" if "compound" in groq_model else "llama"
        return _daily_calls.get(model_key, 0) >= limit


def get_groq_daily_usage() -> dict:
    """Return current daily Groq API usage stats for monitoring."""
    with _daily_counter_lock:
        today = date.today()
        if today == _daily_calls_date:
            calls = dict(_daily_calls)
        else:
            calls = {"compound": 0, "llama": 0}
    return {
        "compound_calls_today": calls.get("compound", 0),
        "compound_daily_limit": _MODEL_RPD_LIMITS["groq/compound"],
        "llama_calls_today": calls.get("llama", 0),
        "llama_daily_limit": _MODEL_RPD_LIMITS["llama-3.3-70b-versatile"],
        "primary_model": "groq/compound",
        "fallback_model": "llama-3.3-70b-versatile",
        "date": str(today),
    }


def _rate_limit_wait():
    """Block until enough time has passed since the last Groq API call."""
    global _last_call_time
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_call_time
        if elapsed < _MIN_CALL_INTERVAL:
            wait = _MIN_CALL_INTERVAL - elapsed
            logger.debug(f"Groq rate limiter: waiting {wait:.1f}s")
            time.sleep(wait)
        _last_call_time = time.monotonic()


# ── Lazy OpenAI client ─────────────────────────────────────────────────────────
_client = None


def _get_client():
    """Lazy-init the OpenAI client pointed at Groq."""
    global _client
    if _client is None and settings.groq_api_key:
        from openai import OpenAI
        _client = OpenAI(
            api_key=settings.groq_api_key,
            base_url=settings.groq_base_url,
        )
    return _client


# ── JSON Sanitizer ─────────────────────────────────────────────────────────────

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
    # Quote unquoted keys (e.g. {action: "BUY"} -> {"action": "BUY"})
    fragment = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', fragment)
    # Normalize single-quoted strings to double-quoted
    fragment = re.sub(r"'([^'\\]*)'", r'"\1"', fragment)
    return fragment


# ── Analyst Prompt ─────────────────────────────────────────────────────────────

def _build_groq_analyst_prompt(
    ticker: str,
    technical_snapshot: Dict,
    macro_news: List[str],
    sector_news: List[str],
    stock_news: List[str],
    portfolio_state: Dict,
    risk_info: Dict,
) -> str:
    """
    Build a compact analyst prompt for Groq.
    Cherry-picks the most signal-rich indicators to keep tokens compact.
    """
    T = technical_snapshot
    def _f(key: str, decimals: int = 2) -> str:
        v = T.get(key)
        return f"{v:.{decimals}f}" if isinstance(v, (int, float)) else "N/A"

    tech_summary = (
        f"Price={_f('close')}, RSI={_f('rsi_14', 1)}, "
        f"MACD={_f('macd_line', 3)}/Signal={_f('macd_signal', 3)}, "
        f"EMA20={_f('ema_20')}, EMA50={_f('ema_50')}, "
        f"BB_upper={_f('bb_upper')}, BB_lower={_f('bb_lower')}, "
        f"ATR={_f('atr_14')}, Vol_ratio={_f('volume_ratio', 2)}, "
        f"ADX={_f('adx', 1)}, Trend={T.get('trend_direction', 'N/A')}"
    )

    cash = portfolio_state.get("cash", 0)
    total_value = portfolio_state.get("total_value", 0)
    holdings_count = len(portfolio_state.get("holdings", []))
    holding_info = "not held"
    for h in portfolio_state.get("holdings", []):
        if h.get("ticker") == ticker:
            qty = h.get("quantity", 0)
            avg = h.get("avg_price", 0)
            holding_info = f"holding {qty}sh@Rs{avg:.0f}"
            break

    def _news(items: List[str], n: int = 3) -> str:
        if not items:
            return "None"
        return " | ".join(h[:80] for h in items[:n])

    prompt = f"""Analyze {ticker} (NSE India) and output a trading decision as JSON.

TECHNICALS: {tech_summary}

NEWS MACRO: {_news(macro_news, 3)}
NEWS SECTOR: {_news(sector_news, 2)}
NEWS STOCK: {_news(stock_news, 3)}

PORTFOLIO: cash=Rs{cash:,.0f}, total=Rs{total_value:,.0f}, positions={holdings_count}, {ticker}={holding_info}
RISK: max_pos={settings.max_single_trade_pct*100:.0f}%, stop_loss={settings.stop_loss_pct*100:.0f}%, sector={risk_info.get('sector','?')}

RULES:
- BUY: strong technicals + positive news + cash available
- SELL: weakness OR bad news OR risk limit breached
- HOLD: default when uncertain
- crisis_detected=true if war/crash/pandemic news; then SELL held stocks, HOLD unowned

Respond with ONLY this JSON (no other text):
{{"action":"HOLD","confidence":0.0,"position_size_pct":0.0,"risk_factors":[],"reasoning":"","news_impact_score":0.0,"crisis_detected":false}}"""

    return prompt


def _repair_truncated_json(raw: str) -> Optional[str]:
    """
    Try to repair JSON that was cut off mid-stream (finish_reason=length).
    Returns repaired string or None if beyond salvaging.
    """
    text = raw.strip()
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None
    text = text[start:]
    depth_brace = 0
    depth_bracket = 0
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace -= 1
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]":
            depth_bracket -= 1
    if in_string:
        text += '"'
    text += "]" * max(0, depth_bracket)
    text += "}" * max(0, depth_brace)
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        return None


# ── Core API Call (single model attempt) ──────────────────────────────────────

def _call_groq_model(
    client,
    model: str,
    prompt: str,
    ticker: str,
    attempt: int,
) -> Optional[dict]:
    """
    Make a single Groq API call for the given model.
    Returns parsed result dict or None on failure.
    Raises exception on API/network errors (caller decides fallback logic).
    """
    model_key = "compound" if "compound" in model else "llama"

    _rate_limit_wait()
    count = _increment_counter(model_key)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a quantitative trading analyst. "
                    "Output ONLY a JSON object with these exact keys: "
                    "action (BUY/SELL/HOLD), confidence (0-1), "
                    "position_size_pct (0-0.2), risk_factors (array of strings), "
                    "reasoning (string), news_impact_score (-1 to 1), "
                    "crisis_detected (true/false). "
                    "No markdown, no text outside the JSON."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=settings.groq_temperature,
        max_tokens=512,
        response_format={"type": "json_object"},  # Groq supports this natively
    )

    choice = response.choices[0]
    finish_reason = choice.finish_reason
    raw_text = choice.message.content

    if not raw_text or raw_text.strip() in ("", "{}"):
        logger.warning(
            f"Groq [{model}] returned empty response for {ticker} "
            f"(attempt {attempt + 1}, finish_reason={finish_reason})"
        )
        return None

    if finish_reason == "length":
        logger.warning(
            f"Groq [{model}] output truncated for {ticker} (finish_reason=length). "
            "Trying to salvage partial JSON."
        )
        raw_text = _repair_truncated_json(raw_text)
        if not raw_text:
            return None

    clean_json = _sanitize_json(raw_text)
    parsed = json.loads(clean_json)  # JSON errors propagate to caller

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
        f"Groq analysis succeeded for {ticker} | "
        f"model={model} attempt={attempt + 1} | "
        f"daily_{model_key}={count} | action={action} conf={confidence:.2f}"
    )

    return {
        "action": action,
        "confidence": confidence,
        "position_size_pct": position_size,
        "risk_factors": parsed.get("risk_factors", []),
        "reasoning": parsed.get("reasoning", ""),
        "news_impact_score": news_impact,
        "crisis_detected": bool(parsed.get("crisis_detected", False)),
        "analyst_model": "groq",
        "groq_model_used": model,
    }


def _is_daily_limit_error(err_str: str) -> bool:
    """
    Return True ONLY if the 429 error is a daily (RPD) exhaustion,
    NOT a temporary per-minute rate limit.
    Groq daily limit messages contain 'requests per day' or 'daily' in them.
    """
    lower = err_str.lower()
    # These phrases indicate true daily RPD exhaustion
    if "requests per day" in lower or "daily request" in lower:
        return True
    # openai/gpt-oss-120b errors are from groq/compound's internal sub-model
    # and are TPM/RPM limits — NOT our daily limit. Do NOT mark compound as exhausted.
    return False


# ── Synchronous Analysis with compound → llama fallback ───────────────────────

def _groq_analyze_sync(
    ticker: str,
    technical_snapshot: Dict,
    macro_news: List[str],
    sector_news: List[str],
    stock_news: List[str],
    portfolio_state: Dict,
    risk_info: Dict,
) -> Optional[dict]:
    """
    Synchronous Groq analysis with compound → llama fallback.

    Returns structured analysis dict or None if all Groq models fail.
    """
    client = _get_client()
    if not client:
        logger.warning("Groq API key not configured.")
        return None

    prompt = _build_groq_analyst_prompt(
        ticker, technical_snapshot, macro_news,
        sector_news, stock_news, portfolio_state, risk_info,
    )

    # Model priority list: compound first, llama as fallback
    MODELS = [
        ("groq/compound", "compound"),
        ("llama-3.3-70b-versatile", "llama"),
    ]

    for model, model_key in MODELS:
        # Skip if this model has hit its true daily request limit
        if _is_model_exhausted(model):
            logger.info(
                f"Groq [{model}] daily limit reached — "
                f"{'skipping to llama fallback' if model_key == 'compound' else 'exhausted'}"
            )
            continue

        try:
            result = _call_groq_model(client, model, prompt, ticker, attempt=0)
            if result:
                return result
            # Empty response → try next model
            logger.warning(f"Groq [{model}] gave empty result for {ticker}, trying next model")

        except json.JSONDecodeError as e:
            # Bad JSON from model — try next model, do NOT mark as exhausted
            logger.warning(
                f"Groq [{model}] JSON parse error for {ticker}: {e} — trying next model"
            )
            continue

        except Exception as e:
            err_str = str(e)
            is_429 = "429" in err_str
            is_daily = _is_daily_limit_error(err_str)

            if is_429 and is_daily:
                # True daily limit hit — mark exhausted and try next model
                logger.warning(
                    f"Groq [{model}] DAILY limit reached for {ticker} — "
                    f"marking exhausted and falling back. Error: {err_str[:150]}"
                )
                with _daily_counter_lock:
                    _daily_calls[model_key] = _MODEL_RPD_LIMITS.get(model, 9999)
                continue

            elif is_429:
                # Temporary RPM/TPM rate limit — skip THIS ticker but don't exhaust model
                logger.warning(
                    f"Groq [{model}] temporary rate limit for {ticker} — "
                    f"skipping to next model (NOT marking exhausted). Error: {err_str[:150]}"
                )
                continue

            elif "decommissioned" in err_str or "not supported" in err_str.lower():
                logger.error(f"Groq [{model}] model decommissioned — skipping. Error: {err_str[:200]}")
                continue
            else:
                logger.warning(f"Groq [{model}] error for {ticker}: {err_str[:200]}")
                # For non-rate errors on compound, try llama before giving up
                continue

    logger.warning(f"Groq: all models failed for {ticker} — falling back to Gemma")
    return None


# ── Async Wrapper ──────────────────────────────────────────────────────────────

async def analyze_with_groq(
    ticker: str,
    technical_snapshot: Dict,
    macro_news: List[str],
    sector_news: List[str],
    stock_news: List[str],
    portfolio_state: Dict,
    risk_info: Dict,
) -> Optional[dict]:
    """
    Primary LLM analyst — Groq API.

    Tries groq/compound first (best quality), falls back to llama-3.3-70b-versatile
    if compound hits its 250 RPD daily limit or fails.

    Returns structured analysis dict or None if all Groq models fail.
    When None, the caller (llm_engine.py) auto-promotes Gemma as analyst.
    """
    logger.info(f"Running Groq analysis for {ticker}")
    return await asyncio.to_thread(
        _groq_analyze_sync,
        ticker, technical_snapshot, macro_news,
        sector_news, stock_news, portfolio_state, risk_info,
    )
