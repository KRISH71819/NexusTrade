"""
Scheduler — orchestrates the autonomous trading pipeline.

Schedule:
  - Hourly (9:15-15:30 IST): Full analysis cycle
  - Every 30 minutes: Risk checks (stop-loss / trailing stop scans)
  - 9:00 AM: Pre-market news scan
  - 3:35 PM: Post-market portfolio snapshot
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import List

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from nifty_stocks import resolve_watchlist
from data_ingestion import (
    bulk_screener,
    ingest_ticker_data,
    get_batch_prices,
    fetch_market_regime,
    fetch_daily_atr,
)
from news_intelligence import fetch_news_intelligence, get_sector
from ml_engine import predict_trend
from llm_engine import analyze_with_gemini
from risk_manager import (
    assess_risk,
    check_stop_losses,
    compute_risk_adjustment,
    detect_underperformers,
    check_profit_taking,
)
from execution_matrix import (
    compute_final_score,
    decide_action,
    build_action_reason,
)
from ledger import (
    get_portfolio,
    execute_buy,
    execute_sell,
    log_hold,
    has_position,
    update_portfolio_valuation,
)
from models import AnalysisResult, TradeAction
from telegram_bot import send_trade_alert

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_analysis_running = False  # prevent concurrent runs
_cancel_requested = False  # allows manual trigger to cancel a stuck cycle

# With Gemma 4 31B (1,500 RPD / 15 RPM), all screened candidates
# get full LLM analysis — no need for a tier limit.


# ═══════════════════════════════════════════════════════════════════════════════
#   MAIN ANALYSIS CYCLE — TWO-TIER ARCHITECTURE
# ═══════════════════════════════════════════════════════════════════════════════

async def run_analysis_cycle(force: bool = False):
    """
    Full LLM analysis cycle — ALL candidates get Gemma 4 31B analysis.

    With 1,500 RPD and 15 RPM, every screened candidate + held stock
    gets full LLM structured analysis for maximum stock selection quality.
    """
    global _analysis_running, _cancel_requested

    if _analysis_running:
        if force:
            logger.warning("Force-cancelling previous analysis cycle...")
            _cancel_requested = True
            # Wait up to 30s for the old cycle to notice and exit
            for _ in range(30):
                await asyncio.sleep(1)
                if not _analysis_running:
                    break
            if _analysis_running:
                logger.error("Old cycle did not stop in time — running anyway")
                _analysis_running = False
        else:
            logger.warning("Analysis cycle already running — skipping")
            return {"status": "skipped", "reason": "already_running"}

    _analysis_running = True
    _cancel_requested = False
    cycle_start = datetime.now(timezone.utc)
    results = []

    try:
        # Import daily budget status for logging
        from llm_engine import get_daily_budget_status
        budget = get_daily_budget_status()

        logger.info("=" * 60)
        logger.info(
            f"ANALYSIS CYCLE START | "
            f"LLM budget: {budget['calls_today']}/{budget['daily_limit']} used today"
        )
        logger.info("=" * 60)

        # ── Step 1: Get portfolio state ──────────────────────────────────
        portfolio = await get_portfolio()
        holdings = portfolio.get("holdings", [])
        held_tickers = [h["ticker"] for h in holdings if h.get("quantity", 0) > 0]

        # ── Step 2: Bulk screen watchlist ────────────────────────────────
        watchlist = resolve_watchlist(settings.watchlist)
        try:
            # bulk_screener does a massive yf.download — run in thread so timeout works
            top_candidates = await asyncio.wait_for(
                asyncio.to_thread(bulk_screener, watchlist, settings.max_candidates_for_ai),
                timeout=180.0,
            )
        except asyncio.TimeoutError:
            logger.error("TIMEOUT: Bulk screener exceeded 180s — using first N tickers as fallback")
            top_candidates = watchlist[:settings.max_candidates_for_ai]

        analysis_tickers = list(set(top_candidates + held_tickers))
        logger.info(
            f"Analyzing {len(analysis_tickers)} tickers "
            f"({len(top_candidates)} screened + {len(held_tickers)} held) — "
            f"ALL getting full LLM analysis"
        )

        # ── Step 2.5: Fetch Market Regime (once per cycle) ───────────────
        # Determines BULLISH/BEARISH based on NIFTY 50 vs 50-day SMA.
        # This is used to gate new BUYs and tighten trailing stops.
        try:
            market_regime_data = await asyncio.to_thread(fetch_market_regime)
        except Exception as e:
            logger.warning(f"Market regime fetch failed: {e}. Defaulting to BULLISH.")
            market_regime_data = {"regime": "BULLISH", "nifty_close": 0, "nifty_sma50": 0, "gap_pct": 0}

        market_regime = market_regime_data["regime"]
        logger.info(
            f"MARKET REGIME: {market_regime} | "
            f"NIFTY={market_regime_data['nifty_close']:,.2f} vs "
            f"SMA50={market_regime_data['nifty_sma50']:,.2f} "
            f"(gap {market_regime_data['gap_pct']:+.2f}%)"
        )

        # ── Step 3: Full LLM analysis for ALL tickers ────────────────────
        # Prioritize held stocks first (need timely SELL decisions),
        # then process new candidates sorted by screener ranking
        ordered_tickers = held_tickers + [t for t in top_candidates if t not in held_tickers]
        # Remove duplicates while preserving order
        seen = set()
        ordered_tickers = [t for t in ordered_tickers if not (t in seen or seen.add(t))]

        for i, ticker in enumerate(ordered_tickers):
            if _cancel_requested:
                logger.warning("Analysis cycle cancelled by new request")
                return {"status": "cancelled", "results": results}
            try:
                # 120s timeout per ticker — prevents a single hung request
                # from freezing the entire cycle for hours
                result = await asyncio.wait_for(
                    _analyze_single_ticker(ticker, portfolio, market_regime),
                    timeout=120.0,
                )
                results.append(result)
            except asyncio.TimeoutError:
                logger.error(f"TIMEOUT: {ticker} analysis exceeded 120s — skipping")
                results.append({"ticker": ticker, "action": "ERROR", "error": "timeout"})
            except Exception as e:
                logger.error(f"Error analyzing {ticker}: {e}", exc_info=True)
                results.append({"ticker": ticker, "error": str(e)})
            # llm_engine rate limiter enforces 4.2s between calls automatically

        # ── Step 3.5: BATCH PORTFOLIO OPTIMIZATION ────────────────────────
        # All tickers scored — now decide optimal portfolio composition.
        # Sells weak holdings first (frees capital), then buys strongest
        # candidates. This replaces the old portfolio rotation logic.
        batch_stats = {"sells": 0, "buys": 0, "partial_sells": 0}
        try:
            batch_stats = await _execute_batch_decisions(results, market_regime)
            logger.info(
                f"BATCH OPTIMIZER: {batch_stats['sells']} full sell(s), "
                f"{batch_stats['partial_sells']} partial sell(s), "
                f"{batch_stats['buys']} buy(s)"
            )
        except Exception as e:
            logger.error(f"Batch optimizer failed: {e}", exc_info=True)

        # ── Step 4: Update portfolio valuation with live prices ──────────
        try:
            held_tickers_now = [
                h["ticker"] for h in (await get_portfolio()).get("holdings", [])
                if h.get("quantity", 0) > 0
            ]
            if held_tickers_now:
                live_prices = get_batch_prices(held_tickers_now)
                await update_portfolio_valuation(live_prices)
                logger.info(f"Portfolio revalued with live prices for {len(live_prices)} holdings")
        except Exception as e:
            logger.warning(f"Could not update portfolio valuation: {e}")

        # ── Summary ──────────────────────────────────────────────────────
        actions = {"BUY": 0, "SELL": 0, "HOLD": 0, "SKIP": 0, "ERROR": 0}
        for r in results:
            action = r.get("action", "ERROR")
            actions[action] = actions.get(action, 0) + 1

        budget_after = get_daily_budget_status()
        elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        logger.info(
            f"ANALYSIS CYCLE COMPLETE in {elapsed:.1f}s — "
            f"BUY: {actions['BUY']}, SELL: {actions['SELL']}, "
            f"HOLD: {actions['HOLD']}, SKIP: {actions.get('SKIP', 0)}, "
            f"ERRORS: {actions.get('ERROR', 0)} | "
            f"LLM calls this cycle: {len(ordered_tickers) - actions.get('SKIP', 0)} | "
            f"Daily budget: {budget_after['calls_today']}/{budget_after['daily_limit']}"
        )
        logger.info("=" * 60)

        return {
            "status": "completed",
            "tickers_analyzed": len(ordered_tickers),
            "llm_analyzed": len(ordered_tickers),
            "actions": actions,
            "elapsed_seconds": round(elapsed, 1),
            "daily_budget": budget_after,
            "results": results,
        }

    except Exception as e:
        logger.error(f"Analysis cycle failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}

    finally:
        _analysis_running = False
        _cancel_requested = False




async def _analyze_single_ticker(
    ticker: str,
    portfolio: dict,
    market_regime: str = "BULLISH",
) -> dict:
    """
    Full analysis pipeline for a single ticker:
    1. Ingest market data + compute indicators
    2. Fetch multi-level news intelligence
    3. Run ML prediction
    4. Run Gemini structured analysis
    5. Assess risk
    6. Compute final score
    7. Decide action (with regime + volume gates)
    8. Return analysis (batch optimizer handles execution)
    """

    # ── 1. Data Ingestion ────────────────────────────────────────────────
    ingestion = await ingest_ticker_data(ticker)
    if ingestion.get("error"):
        return {"ticker": ticker, "action": "ERROR", "error": ingestion["error"]}

    current_price = ingestion.get("latest_price")
    if not current_price or current_price <= 0:
        return {"ticker": ticker, "action": "ERROR", "error": "No price data"}

    indicators = ingestion.get("indicators", {})
    stock_headlines = [n.get("headline", "") for n in ingestion.get("news", []) if isinstance(n, dict)]

    # ── 2. News Intelligence (multi-level) ───────────────────────────────
    try:
        news_intel = await fetch_news_intelligence(ticker, stock_headlines)
    except Exception as e:
        logger.warning(f"News intelligence failed for {ticker}: {e}")
        from models import NewsIntelligence
        news_intel = NewsIntelligence()

    macro_headlines = [n.headline for n in news_intel.macro_news]
    sector_headlines = [n.headline for n in news_intel.sector_news]
    stock_news_headlines = [n.headline for n in news_intel.stock_news]
    all_headlines = macro_headlines + sector_headlines + stock_news_headlines

    # ── 3. ML Prediction ────────────────────────────────────────────────
    try:
        ml_result = await predict_trend(ticker, indicators)
    except Exception as e:
        logger.warning(f"ML prediction failed for {ticker}: {e}")
        ml_result = {"ml_confidence": 0.50, "features_used": {}, "model_info": {"status": "error"}}

    ml_confidence = ml_result["ml_confidence"]

    # ── GATE: Skip stocks with insufficient ML data ──────────────────
    # If ML engine couldn't build a reliable model due to missing data,
    # do NOT waste an LLM call or buy this stock blindly.
    ml_status = ml_result.get("model_info", {}).get("status", "")
    if ml_status in ("insufficient_data", "insufficient_clean_data", "insufficient_training_data"):
        is_holding = await has_position(ticker)
        if not is_holding:
            logger.warning(
                f"SKIPPING {ticker} — ML has {ml_status} "
                f"(not enough bars for reliable prediction). "
                f"Will NOT call LLM or buy."
            )
            return {
                "ticker": ticker,
                "action": "SKIP",
                "reason": ml_status,
                "price": current_price,
            }
        # If we already HOLD it, continue to LLM so we can decide SELL/HOLD

    # ── 4. Gemini Structured Analysis ────────────────────────────────────
    sector = get_sector(ticker)
    sector_exposure = sum(
        1 for h in portfolio.get("holdings", [])
        if get_sector(h.get("ticker", "")) == sector
    )

    risk_info = {
        "sector": sector,
        "sector_exposure_count": sector_exposure,
    }

    try:
        gemini_result = await analyze_with_gemini(
            ticker=ticker,
            technical_snapshot=indicators,
            macro_news=macro_headlines[:8],
            sector_news=sector_headlines[:5],
            stock_news=stock_news_headlines[:5],
            portfolio_state=portfolio,
            risk_info=risk_info,
        )
    except Exception as e:
        logger.warning(f"Gemini analysis failed for {ticker}: {e}")
        gemini_result = {
            "action": "HOLD", "confidence": 0.5, "position_size_pct": 0.0,
            "risk_factors": [], "reasoning": f"Gemini unavailable: {e}",
            "news_impact_score": 0.0, "crisis_detected": False,
        }

    gemini_confidence = gemini_result["confidence"]
    gemini_action = gemini_result["action"]
    news_impact_score = gemini_result["news_impact_score"]
    crisis_from_gemini = gemini_result["crisis_detected"]
    crisis_from_news = news_intel.crisis_detected

    crisis_detected = crisis_from_gemini or crisis_from_news

    # ── 5. Risk Assessment ───────────────────────────────────────────────
    risk_assessment = assess_risk(
        ticker=ticker,
        current_price=current_price,
        portfolio=portfolio,
        ml_confidence=ml_confidence,
        gemini_confidence=gemini_confidence,
        crisis_detected=crisis_detected,
    )

    risk_adjustment = compute_risk_adjustment(risk_assessment)

    # ── 6. Compute Final Score ───────────────────────────────────────────
    final_score = compute_final_score(
        gemini_confidence=gemini_confidence,
        ml_confidence=ml_confidence,
        news_impact_score=news_impact_score,
        risk_adjustment=risk_adjustment,
    )

    # ── 7. Decide Action (with regime + volume gates) ──────────────────
    is_holding = await has_position(ticker)
    volume_ratio = indicators.get("volume_ratio", 1.0)

    action = decide_action(
        final_score=final_score,
        gemini_action=gemini_action,
        crisis_detected=crisis_detected,
        risk_assessment=risk_assessment,
        has_position=is_holding,
        market_regime=market_regime,
        volume_ratio=volume_ratio,
    )

    action_reason = build_action_reason(
        action=action,
        final_score=final_score,
        gemini_confidence=gemini_confidence,
        ml_confidence=ml_confidence,
        news_impact_score=news_impact_score,
        risk_adjustment=risk_adjustment,
        gemini_reasoning=gemini_result.get("reasoning", ""),
        risk_flags=risk_assessment.risk_flags,
        crisis_detected=crisis_detected,
        market_regime=market_regime,
        volume_ratio=volume_ratio,
    )

    # ── Build AnalysisResult ─────────────────────────────────────────────
    analysis = AnalysisResult(
        ticker=ticker,
        current_price=current_price,
        ml_confidence=ml_confidence,
        ml_features_used=ml_result.get("features_used", {}),
        news_headlines=all_headlines[:10],
        gemini_sentiment_score=news_impact_score,
        gemini_explanation=gemini_result.get("reasoning", ""),
        gemini_confidence=gemini_confidence,
        gemini_risk_factors=gemini_result.get("risk_factors", []),
        gemini_position_size_pct=gemini_result.get("position_size_pct", 0.0),
        crisis_detected=crisis_detected,
        risk_assessment=risk_assessment.model_dump(),
        final_score=final_score,
        action=action,
        action_reason=action_reason,
    )

    # ── 8. Return analysis (NO execution — batch optimizer handles trades) ──
    # Log the scoring decision for transparency, but don't execute any trade.
    # The batch optimizer in _execute_batch_decisions() will rank all stocks
    # and execute sells/buys in optimal order after ALL tickers are scored.

    logger.info(
        f"[SCORED:{action.value}] {ticker} @ Rs{current_price:.2f} | "
        f"Score: {final_score:.2f} | ML: {ml_confidence:.2f} | "
        f"Gemini: {gemini_confidence:.2f} | News: {news_impact_score:+.2f} | "
        f"Crisis: {crisis_detected}"
    )

    return {
        "ticker": ticker,
        "action": action.value,
        "price": current_price,
        "final_score": round(final_score, 3),
        "ml_confidence": round(ml_confidence, 3),
        "gemini_confidence": round(gemini_confidence, 3),
        "news_impact": round(news_impact_score, 3),
        "crisis": crisis_detected,
        "analysis": analysis,              # full AnalysisResult for trade execution
        "risk_assessment": risk_assessment, # RiskAssessment for position sizing
        "gemini_result": gemini_result,     # Gemini output for position sizing
    }


# ═══════════════════════════════════════════════════════════════════════════════
#   BATCH PORTFOLIO OPTIMIZER — Score everything, then trade smart
# ═══════════════════════════════════════════════════════════════════════════════

async def _execute_batch_decisions(cycle_results: list, market_regime: str = "BULLISH") -> dict:
    """
    Batch Portfolio Optimizer — the brain of the trading system.

    Instead of executing trades greedily as each ticker is analyzed,
    this function takes ALL scored results and makes optimal decisions:

    Phase 1: CLASSIFY — sort held stocks into KEEP / REDUCE / SELL
    Phase 2: SELL — execute sells first to free capital
    Phase 3: BUY — buy strongest candidates with available capital

    This ensures:
    - The portfolio ALWAYS holds the highest-scoring stocks available
    - Cash is never wasted on early weak signals while better ones wait
    - Partial sells free capital without dumping recovering stocks
    - No extra API calls — uses scores already computed this cycle
    """
    portfolio = await get_portfolio()
    holdings = portfolio.get("holdings", [])
    held_tickers = {h["ticker"] for h in holdings if h.get("quantity", 0) > 0}

    # Filter to valid results only (must have score, price, and analysis object)
    scored = [
        r for r in cycle_results
        if r.get("final_score") is not None
        and r.get("price")
        and r.get("analysis")
    ]

    if not scored:
        logger.info("BATCH OPTIMIZER: No valid scored results to process")
        return {"sells": 0, "buys": 0, "partial_sells": 0}

    # ── PHASE 1: CLASSIFY ────────────────────────────────────────────────
    full_sells = []       # held stocks to fully exit
    partial_sells = []    # held stocks to reduce exposure
    buy_candidates = []   # new stocks to buy
    keeps = []            # held stocks to keep as-is

    for r in scored:
        ticker = r["ticker"]
        score = r["final_score"]
        is_held = ticker in held_tickers
        crisis = r.get("crisis", False)
        action_recommended = r.get("action", "HOLD")

        if is_held:
            # ── Decide what to do with HELD stocks ────────────────────
            if crisis or score < settings.score_weak_hold:
                # Weak stock or crisis → full sell to free capital
                full_sells.append(r)
                logger.info(
                    f"  CLASSIFY {ticker}: FULL SELL "
                    f"(score={score:.2f} < {settings.score_weak_hold}, crisis={crisis})"
                )
            elif score < settings.score_strong_hold:
                # Mediocre stock → partial sell (free some capital, keep upside)
                partial_sells.append(r)
                logger.info(
                    f"  CLASSIFY {ticker}: PARTIAL SELL "
                    f"(score={score:.2f}, between {settings.score_weak_hold}-{settings.score_strong_hold})"
                )
            else:
                # Strong stock → keep entire position
                keeps.append(r)
                logger.debug(f"  CLASSIFY {ticker}: KEEP (score={score:.2f})")
        else:
            # ── New candidate — only consider if score qualifies for BUY ──
            if action_recommended == "BUY" and score >= 0.60 and not crisis:
                buy_candidates.append(r)

    # Sort buy candidates by score descending (best opportunities first)
    buy_candidates.sort(key=lambda r: r["final_score"], reverse=True)

    logger.info(
        f"BATCH OPTIMIZER PLAN: "
        f"{len(full_sells)} full sell(s), {len(partial_sells)} partial sell(s), "
        f"{len(keeps)} keep(s), {len(buy_candidates)} buy candidate(s)"
    )

    stats = {"sells": 0, "buys": 0, "partial_sells": 0}

    # ── PHASE 2: EXECUTE SELLS FIRST (frees capital for buys) ────────────
    for r in full_sells:
        ticker = r["ticker"]
        analysis = r["analysis"]
        price = r["price"]

        trade = await execute_sell(ticker, price, analysis)
        if "error" not in trade:
            asyncio.create_task(send_trade_alert(trade))
            stats["sells"] += 1
            logger.info(
                f"  BATCH SELL: {ticker} @ Rs.{price:.2f} "
                f"(score {r['final_score']:.2f} < {settings.score_weak_hold})"
            )
        else:
            logger.warning(f"  BATCH SELL FAILED: {ticker} — {trade.get('error')}")

    for r in partial_sells:
        ticker = r["ticker"]
        analysis = r["analysis"]
        price = r["price"]

        # Find holding to calculate 50% sell
        holding = next((h for h in holdings if h["ticker"] == ticker), None)
        if holding and holding.get("quantity", 0) > 1:
            sell_qty = max(1, holding["quantity"] // 2)
            trade = await execute_sell(ticker, price, analysis, quantity=sell_qty)
            if "error" not in trade:
                asyncio.create_task(send_trade_alert(trade))
                stats["partial_sells"] += 1
                logger.info(
                    f"  BATCH PARTIAL SELL: {ticker} — sold {sell_qty}/{holding['quantity']} shares "
                    f"(score {r['final_score']:.2f}, freeing capital for stronger stocks)"
                )
            else:
                logger.warning(f"  BATCH PARTIAL SELL FAILED: {ticker} — {trade.get('error')}")

    # ── PHASE 3: EXECUTE BUYS (strongest scores first, with SWAP logic) ───
    # Re-fetch portfolio after sells to get updated cash balance
    portfolio = await get_portfolio()
    current_holdings_count = len([
        h for h in portfolio.get("holdings", [])
        if h.get("quantity", 0) > 0
    ])

    # Build a score map of all current holdings from this cycle's results
    # Used for swap decisions when cash is insufficient
    holding_scores = {}
    for r in scored:
        if r["ticker"] in held_tickers and r.get("final_score") is not None:
            holding_scores[r["ticker"]] = r

    for r in buy_candidates:
        # Check position limit before each buy
        if current_holdings_count >= settings.max_open_positions:
            logger.info(
                f"  BATCH BUY STOP: Max positions ({settings.max_open_positions}) reached. "
                f"Skipping remaining {len(buy_candidates) - buy_candidates.index(r)} candidates."
            )
            break

        ticker = r["ticker"]
        analysis = r["analysis"]
        price = r["price"]
        risk_assessment = r.get("risk_assessment")
        gemini_result = r.get("gemini_result", {})

        # Position sizing: use the most conservative of all recommendations
        max_pos_pct = min(
            gemini_result.get("position_size_pct", settings.max_position_pct),
            risk_assessment.max_allowed_position_pct if risk_assessment else settings.max_position_pct,
            settings.max_single_trade_pct,  # per-trade diversification cap
        )

        # ── Fetch daily ATR for volatility-adjusted position sizing ─────
        daily_atr = None
        try:
            daily_atr = await asyncio.to_thread(fetch_daily_atr, ticker)
        except Exception as e:
            logger.debug(f"Could not fetch daily ATR for {ticker}: {e}")

        trade = await execute_buy(
            ticker=ticker,
            price=price,
            analysis=analysis,
            max_position_pct=max_pos_pct,
            atr=daily_atr,
        )

        if "error" not in trade:
            asyncio.create_task(send_trade_alert(trade))
            stats["buys"] += 1
            current_holdings_count += 1
            logger.info(
                f"  BATCH BUY: {ticker} @ Rs.{price:.2f} "
                f"(score {r['final_score']:.2f}, rank #{buy_candidates.index(r) + 1})"
            )
        elif trade.get("error") == "Insufficient funds":
            # ── SWAP LOGIC: Sell weakest holding to fund this buy ─────
            # Find the current holding with the lowest score from this cycle
            swappable = []
            current_portfolio = await get_portfolio()
            current_holdings = current_portfolio.get("holdings", [])

            for h in current_holdings:
                h_ticker = h.get("ticker", "")
                h_qty = h.get("quantity", 0)
                if h_qty <= 0 or h_ticker == ticker:
                    continue

                # Check holding age — must be held >= rotation_min_hold_hours
                bought_at = h.get("bought_at")
                if bought_at:
                    if isinstance(bought_at, str):
                        try:
                            from datetime import datetime as dt_parse
                            bought_at = dt_parse.fromisoformat(bought_at)
                        except (ValueError, TypeError):
                            bought_at = None
                    if bought_at and bought_at.tzinfo is None:
                        from datetime import timezone as tz
                        bought_at = bought_at.replace(tzinfo=tz.utc)
                    if bought_at:
                        from datetime import datetime as dt_now, timezone as tz_now
                        age_hours = (dt_now.now(tz_now.utc) - bought_at).total_seconds() / 3600
                        if age_hours < settings.rotation_min_hold_hours:
                            continue

                # Get this holding's score from the current cycle
                h_score_data = holding_scores.get(h_ticker)
                if h_score_data:
                    swappable.append({
                        "ticker": h_ticker,
                        "score": h_score_data["final_score"],
                        "holding": h,
                        "result": h_score_data,
                    })

            if not swappable:
                logger.warning(
                    f"  SWAP FAILED: No swappable holdings for {ticker} "
                    f"(all too new or no scores)"
                )
                continue

            # Find the weakest holding
            swappable.sort(key=lambda x: x["score"])
            weakest = swappable[0]
            score_gap = r["final_score"] - weakest["score"]

            if score_gap >= settings.rotation_min_score_gap:
                # Execute the swap: SELL weak, then BUY strong
                swap_reason = (
                    f"CAPITAL SWAP: Sold {weakest['ticker']} "
                    f"(score {weakest['score']:.2f}) to fund "
                    f"{ticker} (score {r['final_score']:.2f}, "
                    f"gap {score_gap:.2f} >= {settings.rotation_min_score_gap})"
                )
                logger.info(f"  {swap_reason}")

                # Build analysis for the sell
                swap_analysis = AnalysisResult(
                    ticker=weakest["ticker"],
                    current_price=weakest["result"]["price"],
                    ml_confidence=weakest["result"].get("ml_confidence", 0),
                    gemini_sentiment_score=0.0,
                    gemini_explanation=swap_reason,
                    gemini_confidence=0.0,
                    final_score=weakest["score"],
                    action=TradeAction.SELL,
                    action_reason=swap_reason,
                )

                sell_trade = await execute_sell(
                    weakest["ticker"],
                    weakest["result"]["price"],
                    swap_analysis,
                )

                if "error" not in sell_trade:
                    asyncio.create_task(send_trade_alert(sell_trade))
                    stats["sells"] += 1
                    current_holdings_count -= 1

                    # Now retry the buy with freed capital
                    retry_trade = await execute_buy(
                        ticker=ticker,
                        price=price,
                        analysis=analysis,
                        max_position_pct=max_pos_pct,
                        atr=daily_atr,
                    )

                    if "error" not in retry_trade:
                        asyncio.create_task(send_trade_alert(retry_trade))
                        stats["buys"] += 1
                        current_holdings_count += 1
                        logger.info(
                            f"  SWAP BUY: {ticker} @ Rs.{price:.2f} "
                            f"(funded by selling {weakest['ticker']})"
                        )
                    else:
                        logger.warning(
                            f"  SWAP BUY FAILED after sell: {ticker} — "
                            f"{retry_trade.get('error')}"
                        )
                else:
                    logger.warning(
                        f"  SWAP SELL FAILED: {weakest['ticker']} — "
                        f"{sell_trade.get('error')}"
                    )
            else:
                logger.info(
                    f"  SWAP SKIPPED: {ticker} (score {r['final_score']:.2f}) vs "
                    f"weakest {weakest['ticker']} (score {weakest['score']:.2f}) — "
                    f"gap {score_gap:.2f} < {settings.rotation_min_score_gap}"
                )
        else:
            logger.warning(f"  BATCH BUY FAILED: {ticker} — {trade.get('error')}")

        # Re-fetch portfolio to get updated cash for next buy
        portfolio = await get_portfolio()

    # Log final portfolio state
    final_portfolio = await get_portfolio()
    logger.info(
        f"BATCH OPTIMIZER DONE | Cash: Rs.{final_portfolio['cash']:,.2f} | "
        f"Holdings: {len([h for h in final_portfolio.get('holdings', []) if h.get('quantity', 0) > 0])} | "
        f"Total: Rs.{final_portfolio.get('total_value', 0):,.2f}"
    )

    return stats


# ═══════════════════════════════════════════════════════════════════════════════
#   RISK CHECK (runs every 30 minutes)
# ═══════════════════════════════════════════════════════════════════════════════

async def run_risk_check():
    """
    Comprehensive risk scan — runs every 30 minutes:
      1. Stop-loss / trailing stop checks (instant sell on breach)
      2. Underperformer detection (sell stagnant/declining stocks)
      3. Profit-taking (partial sell on big winners to lock gains)
    """
    try:
        portfolio = await get_portfolio()
        holdings = portfolio.get("holdings", [])

        if not holdings:
            return

        held_tickers = [h["ticker"] for h in holdings if h.get("quantity", 0) > 0]
        if not held_tickers:
            return

        # Fetch live prices for all holdings (sync yf.download → run in thread)
        live_prices = await asyncio.to_thread(get_batch_prices, held_tickers)

        # Update valuation with live prices
        await update_portfolio_valuation(live_prices)

        total_sells = 0

        # ── 1. Stop-Loss / Trailing Stop (hard rules — execute immediately) ──
        # In BEARISH regime, trailing stop tightens from 8% to 4%
        try:
            regime_data = await asyncio.to_thread(fetch_market_regime)
            risk_regime = regime_data.get("regime", "BULLISH")
        except Exception:
            risk_regime = "BULLISH"

        stop_signals = check_stop_losses(portfolio, live_prices, market_regime=risk_regime)

        for signal in stop_signals:
            ticker = signal["ticker"]
            logger.warning(f"RISK ALERT: {signal['reason']}")

            analysis = AnalysisResult(
                ticker=ticker,
                current_price=signal["price"],
                ml_confidence=0.0,
                gemini_sentiment_score=-1.0,
                gemini_explanation=signal["reason"],
                gemini_confidence=0.0,
                final_score=0.0,
                action=TradeAction.SELL,
                action_reason=signal["reason"],
            )
            trade = await execute_sell(ticker, signal["price"], analysis)
            if "error" not in trade:
                asyncio.create_task(send_trade_alert(trade))
                total_sells += 1

        # ── 2. Underperformer Detection (sell weak stocks) ───────────────────
        # Re-fetch portfolio after potential stop-loss sells
        if stop_signals:
            portfolio = await get_portfolio()

        # detect_underperformers calls yf.download per holding — run in thread
        underperformer_signals = await asyncio.to_thread(detect_underperformers, portfolio, live_prices)

        for signal in underperformer_signals:
            ticker = signal["ticker"]
            # Skip if already sold by stop-loss above
            if ticker in [s["ticker"] for s in stop_signals]:
                continue

            logger.warning(f"UNDERPERFORMER: {signal['reason']}")

            analysis = AnalysisResult(
                ticker=ticker,
                current_price=signal["price"],
                ml_confidence=0.0,
                gemini_sentiment_score=-0.5,
                gemini_explanation=signal["reason"],
                gemini_confidence=0.0,
                final_score=0.1,
                action=TradeAction.SELL,
                action_reason=signal["reason"],
            )

            if signal.get("sell_all", True):
                trade = await execute_sell(ticker, signal["price"], analysis)
            else:
                trade = await execute_sell(
                    ticker, signal["price"], analysis,
                    quantity=signal.get("sell_quantity"),
                )

            if "error" not in trade:
                asyncio.create_task(send_trade_alert(trade))
                total_sells += 1

        # ── 3. Profit-Taking (partial sell on winners) ───────────────────────
        if underperformer_signals:
            portfolio = await get_portfolio()

        profit_signals = check_profit_taking(portfolio, live_prices)

        for signal in profit_signals:
            ticker = signal["ticker"]
            # Skip if already handled above
            sold_tickers = (
                [s["ticker"] for s in stop_signals] +
                [s["ticker"] for s in underperformer_signals]
            )
            if ticker in sold_tickers:
                continue

            logger.info(f"PROFIT TAKING: {signal['reason']}")

            analysis = AnalysisResult(
                ticker=ticker,
                current_price=signal["price"],
                ml_confidence=0.5,
                gemini_sentiment_score=0.5,
                gemini_explanation=signal["reason"],
                gemini_confidence=0.5,
                final_score=0.5,
                action=TradeAction.SELL,
                action_reason=signal["reason"],
            )

            trade = await execute_sell(
                ticker, signal["price"], analysis,
                quantity=signal.get("sell_quantity"),
            )
            if "error" not in trade:
                asyncio.create_task(send_trade_alert(trade))
                total_sells += 1

                # ── Update profit-taking tier tracking on the holding ────
                # This prevents the same tier from firing again next cycle,
                # and sets the break-even stop lock after tier 1.
                tier = signal.get("tier")
                if tier:
                    try:
                        from database import get_portfolio_collection
                        coll = get_portfolio_collection()
                        update_ops = {
                            "$addToSet": {"holdings.$.profit_taken_tiers": tier}
                        }
                        # If tier 1 fired, lock stop at entry price
                        lock_stop = signal.get("lock_stop_at")
                        if lock_stop:
                            update_ops["$set"] = {
                                "holdings.$.locked_stop_price": lock_stop
                            }
                        await coll.update_one(
                            {"holdings.ticker": ticker},
                            update_ops,
                        )
                        logger.info(
                            f"Updated {ticker} profit tier tracking: "
                            f"added '{tier}'"
                            f"{f', locked stop at Rs.{lock_stop:.2f}' if lock_stop else ''}"
                        )
                    except Exception as e:
                        logger.warning(f"Could not update tier tracking for {ticker}: {e}")

        # ── Summary ──────────────────────────────────────────────────────────
        if total_sells > 0:
            logger.info(
                f"Risk check complete: {total_sells} sell(s) executed — "
                f"stop-loss: {len(stop_signals)}, "
                f"underperformers: {len(underperformer_signals)}, "
                f"profit-taking: {len(profit_signals)}"
            )
        else:
            logger.debug("Risk check complete: all holdings healthy")

    except Exception as e:
        logger.error(f"Risk check failed: {e}", exc_info=True)


# ═══════════════════════════════════════════════════════════════════════════════
#   SCHEDULER SETUP
# ═══════════════════════════════════════════════════════════════════════════════

def start_scheduler():
    """
    Initialize and start the APScheduler with all scheduled jobs.
    """
    global _scheduler
    if _scheduler is not None:
        logger.warning("Scheduler already running")
        return _scheduler

    _scheduler = AsyncIOScheduler(timezone=settings.scheduler_timezone)

    # Hourly analysis during market hours (9:15 AM - 3:30 PM IST)
    _scheduler.add_job(
        run_analysis_cycle,
        CronTrigger(
            hour=f"{settings.market_open_hour}-{settings.market_close_hour}",
            minute="15",
            day_of_week="mon-fri",
            timezone=settings.scheduler_timezone,
        ),
        id="hourly_analysis",
        name="Hourly Analysis Cycle",
        max_instances=1,
    )

    # Risk checks every 30 minutes during market hours
    _scheduler.add_job(
        run_risk_check,
        CronTrigger(
            hour=f"{settings.market_open_hour}-{settings.market_close_hour}",
            minute="*/30",
            day_of_week="mon-fri",
            timezone=settings.scheduler_timezone,
        ),
        id="risk_check",
        name="Risk Check (Stop-Loss / Trailing Stop)",
        max_instances=1,
    )

    _scheduler.start()
    logger.info(
        f"Scheduler started — Analysis hourly {settings.market_open_hour}:15-"
        f"{settings.market_close_hour}:15 IST, Risk checks every 30min"
    )

    return _scheduler


def stop_scheduler():
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")
