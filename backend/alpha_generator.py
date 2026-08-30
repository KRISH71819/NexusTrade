"""
Alpha Generator (Phase 2) — LLM as hypothesis engine.
The LLM writes candidate alpha formulas in the sandbox DSL. It never decides
trades: every expression must pass dsl.validate_expression() and the
walk-forward gate before it means anything.
Offline only (never import from main.py / scheduler.py).
"""
import json
import logging
import re
import time

from config import settings

logger = logging.getLogger(__name__)

DSL_SPEC = """You are a quantitative researcher writing trading rules for NSE Indian equities.
Output rules in this EXACT expression language (evaluated per ticker on daily bars):

COLUMNS: open, high, low, close, volume
FUNCTIONS (all causal, n = window in days):
  sma(x, n), ema(x, n), std(x, n), delta(x, n), zscore(x, n),
  rank(x, n) (rolling percentile 0..1), rsi(x, n) (0..100),
  macd(x), macd_hist(x), volume_ratio(x, n), abs(x)
OPERATORS: + - * / , comparisons (> < >= <= == !=), logic (and, or, not)
Windows must be integers between 2 and 250.

SEMANTICS: expression value = SCORE. score > 0 => LONG next day, score <= 0 => CASH.
HARD RULES:
1. Use ONLY the names above. No imports, no indexing, no other functions.
2. Be selective: aim for LONG on roughly 20-70% of days (not always, not never).
3. Avoid churn: round-trip cost is ~1.1%, so rules that flip daily die in costs.
   Prefer entry windows >= 10 and hold conditions that persist for weeks.
4. Encode an economic idea: trend following, panic reversal, volume confirmation,
   volatility-contraction breakout, sector-free momentum quality. Not random math.
5. TURNOVER IS THE ENEMY: costs are 0.544%/side. Rules that flip on daily
   crosses (close vs sma) die in costs — measured: a daily-cross rule lost
   ~30%/yr to turnover despite a +13.6% gross edge. Design weekly-cadence,
   buffered, selective rules: entry buffers (close > sma(close,20)*1.02),
   confirmation (delta(close,5) > 0), selective exposure 20-60%.

STRUCTURAL DNA PREFERENCES:
- PREFER cross-sectional ranking DNA: rank(<slow signal>) then hold top-N, rebalance >= 40 trading days. This is the ONLY family that has ever passed the gates.
- PREFER slow signals: lookbacks of 60, 120, 200, 252 days.
- TARGET annual turnover < 12x.

HARD REJECTIONS (DO NOT write these):
- No fast oscillator crosses (lookbacks < 60 days) as entry triggers — they churn fees.
- No conjunctions of 3+ tight conditions (e.g. volume spike AND narrow range AND trend filter) — they never fire and produce zero-trade formulas.
- No threshold-based entry rules when a ranking alternative exists.
"""


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    return text


def _parse_candidates(raw: str) -> list:
    text = _strip_fences(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return []
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        data = data.get("alphas", [])
    out = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and item.get("expression"):
            out.append({
                "name": re.sub(r"[^A-Za-z0-9_]", "", str(item.get("name", "llm_alpha")))[:40] or "llm_alpha",
                "expression": str(item["expression"])[:300],
                "hypothesis": str(item.get("hypothesis", ""))[:300],
            })
    return out


BASE_RATES = """EMPIRICAL BASE RATES (clean 2010+ window, equal-weight large-caps, costs 0.544%/side):
- buy&hold: ~+19%/yr, Sharpe ~1.05, maxDD ~-41%.
- A rule long ~95% of days is buy&hold with extra churn — it WILL fail the gate.
- To pass you need SELECTIVITY (long 20-70% of days) and/or much smaller drawdown.
- Use ONE trend anchor + ONE selective entry condition. Holds of weeks, not days.

SCORE SEMANTICS (v2): the expression value is used two ways:
 - timing mode: score > 0 => long that ticker;
 - ranking mode: the HIGHEST scores are bought (top-N cross-sectionally,
   rebalanced monthly). This is the primary mode.
Design scores where HIGHER = stronger expected return over the next 1-4 weeks.
EMPIRICAL LAW (measured on this universe, costs 0.544%/side):
 - slow scores (sma/ema 20-120, 60d momentum) survive costs;
 - daily-flip conditions die in costs;
 - buy&hold = Sharpe 1.06; any active rule must earn its costs."""


def _get_best_near_miss_line(memory: list) -> str:
    """Find the best failed candidate with sharpe > 0, or default to run 29fb3228a2fb near-miss."""
    best_cand = None
    best_sharpe = -999.0
    for m in memory:
        if m.get("status") != "approved":
            met = m.get("metrics") or {}
            sh = met.get("sharpe")
            try:
                sh_val = float(sh)
                if sh_val > 0 and sh_val > best_sharpe:
                    best_sharpe = sh_val
                    best_cand = m
            except (ValueError, TypeError):
                continue

    if best_cand:
        met = best_cand.get("metrics") or {}
        name = best_cand.get("name", "near_miss")
        sh = met.get("sharpe", best_sharpe)
        to = met.get("ann_turnover", "?")
        return (
            f"PREVIOUS BEST NEAR-MISS (mutate TOWARD this family, preserve its low-turnover DNA): "
            f"name='{name}', sharpe={sh}, turnover={to}x/yr — this formula's expression was the "
            f"closest to passing. Keep its low turnover structure while improving Sharpe."
        )

    return (
        "PREVIOUS BEST NEAR-MISS (mutate TOWARD this family, preserve its low-turnover DNA): "
        "name='Vol_Contraction_Breakout', sharpe=0.85, turnover=5.7x/yr — this formula's expression "
        "was the closest to passing. Keep its low turnover structure while improving Sharpe."
    )


def _build_prompt(count: int, memory: list) -> str:
    lines = [
        DSL_SPEC,
        BASE_RATES,
        _get_best_near_miss_line(memory),
        f"Write {count} NEW candidate rules.",
        'Respond with ONLY JSON: {"alphas": [{"name": str, "expression": str, "hypothesis": str}]}',
    ]
    rejected = [m for m in memory if m.get("status") != "approved"][:5]
    if rejected:
        lines.append("DO NOT REPEAT THESE FAMILIES (failed previous attempts — mutate AWAY from them):")
        for m in rejected:
            met = m.get("metrics") or {}
            to_val = met.get("ann_turnover", "?")
            lines.append(
                f"- {m.get('expression')} | sharpe={met.get('sharpe', '?')} "
                f"maxDD={met.get('max_dd_pct', '?')}% turnover={to_val}x/yr"
            )
    return "\n".join(lines)


def _call_groq(prompt: str):
    if not settings.groq_api_key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.groq_api_key, base_url=settings.groq_base_url)
        for model in (settings.groq_compound_model, settings.groq_fallback_model):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.7,
                    max_tokens=700,
                    response_format={"type": "json_object"},
                )
                text = resp.choices[0].message.content
                if text and text.strip() not in ("", "{}"):
                    logger.info(f"Alpha generator used Groq model {model}")
                    return text
            except Exception as e:
                logger.warning(f"Groq {model} failed for generation: {str(e)[:120]}")
                time.sleep(2.0)
    except Exception as e:
        logger.warning(f"Groq unavailable for generation: {str(e)[:120]}")
    return None


def _call_gemma(prompt: str):
    if not settings.gemini_api_key:
        return None
    try:
        from google import genai
        client = genai.Client(api_key=settings.gemini_api_key)
        resp = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.7,
            ),
        )
        logger.info(f"Alpha generator used Gemma model {settings.gemini_model}")
        return resp.text
    except Exception as e:
        logger.warning(f"Gemma failed for generation: {str(e)[:120]}")
    return None


def generate_candidates(count: int = 3, memory: list | None = None) -> list:
    count = max(1, min(6, count))
    prompt = _build_prompt(count, memory or [])
    raw = _call_groq(prompt) or _call_gemma(prompt)
    if not raw:
        logger.error("No LLM available for alpha generation (check GROQ_API_KEY / GEMINI_API_KEY)")
        return []
    cands = _parse_candidates(raw)
    logger.info(f"LLM proposed {len(cands)} candidate formula(s)")
    return cands


_REVISION_PROMPT = """You are a quantitative researcher revising a trading rule for NSE equities.
ORIGINAL EXPRESSION: {expr}
ORIGINAL HYPOTHESIS: {hypothesis}
CRITIC FINDINGS:
{reasons}

Fix ONLY the problems the critic named. Keep the same economic idea.
Same causal DSL whitelist. Selective exposure 20-70%. No daily-flip conditions.
Respond with ONLY JSON: {{"alphas": [{{"name": str, "expression": str, "hypothesis": str}}]}}"""


def revise_candidate(candidate: dict, critique: dict) -> dict | None:
    """One revision attempt using the critic's findings. Returns revised candidate or None."""
    prompt = _REVISION_PROMPT.format(
        expr=candidate.get("expression", ""),
        hypothesis=candidate.get("hypothesis", ""),
        reasons="\n".join(f"- {r}" for r in critique.get("reasons", [])) or "- (no details)",
    )
    raw = _call_groq(prompt) or _call_gemma(prompt)
    if not raw:
        return None
    parsed = _parse_candidates(raw)
    return parsed[0] if parsed else None
