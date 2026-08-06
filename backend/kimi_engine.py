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
    """
    Build a compact analyst prompt for Kimi K3.
    Kept short intentionally — the free tier has limited output tokens.
    We cherry-pick the most signal-rich indicators instead of dumping all.
    """

    # ── Key technical indicators only (avoids token bloat) ──────────────
    T = technical_snapshot  # shorthand
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

    # ── Portfolio context ────────────────────────────────────────────────
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

    # ── News (trim to 3 headlines each, 80 chars max per headline) ───────
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
    Attempts to close any open braces/brackets so json.loads can parse it.
    Returns repaired string or None if beyond salvaging.
    """
    text = raw.strip()
    if not text:
        return None

    # Find start of JSON object
    start = text.find("{")
    if start == -1:
        return None
    text = text[start:]

    # Count open braces/brackets
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

    # Close any open string
    if in_string:
        text += '"'

    # Close open brackets/braces
    text += "]" * max(0, depth_bracket)
    text += "}" * max(0, depth_brace)

    # Try parsing
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        return None


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

    max_retries = 1  # 1 try only — if Kimi fails, fall back to Gemma immediately

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
                temperature=settings.kimi_temperature,
                max_tokens=512,
                response_format={"type": "json_object"},  # Groq supports this natively
            )

            choice = response.choices[0]
            finish_reason = choice.finish_reason
            raw_text = choice.message.content

            if not raw_text or raw_text.strip() in ("", "{}"):
                logger.warning(
                    f"Kimi returned empty response for {ticker} "
                    f"(attempt {attempt + 1}, finish_reason={finish_reason})"
                )
                continue

            if finish_reason == "length":
                # Response was truncated — try to salvage partial JSON
                logger.warning(
                    f"Kimi output truncated for {ticker} (finish_reason=length). "
                    "Trying to salvage partial JSON."
                )
                raw_text = _repair_truncated_json(raw_text)
                if not raw_text:
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
