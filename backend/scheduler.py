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
        top_candidates = bulk_screener(watchlist, max_results=settings.max_candidates_for_ai)

        analysis_tickers = list(set(top_candidates + held_tickers))
        logger.info(
            f"Analyzing {len(analysis_tickers)} tickers "
            f"({len(top_candidates)} screened + {len(held_tickers)} held) — "
            f"ALL getting full LLM analysis"
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
                    _analyze_single_ticker(ticker, portfolio),
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
        actions = {"BUY": 0, "SELL": 0, "HOLD": 0, "ERROR": 0}
        for r in results:
            action = r.get("action", "ERROR")
            actions[action] = actions.get(action, 0) + 1

        budget_after = get_daily_budget_status()
        elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        logger.info(
            f"ANALYSIS CYCLE COMPLETE in {elapsed:.1f}s — "
            f"BUY: {actions['BUY']}, SELL: {actions['SELL']}, "
            f"HOLD: {actions['HOLD']}, ERRORS: {actions.get('ERROR', 0)} | "
            f"LLM calls this cycle: {len(ordered_tickers)} | "
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




async def _analyze_single_ticker(ticker: str, portfolio: dict) -> dict:
    """
    Full analysis pipeline for a single ticker:
    1. Ingest market data + compute indicators
    2. Fetch multi-level news intelligence
    3. Run ML prediction
    4. Run Gemini structured analysis
    5. Assess risk
    6. Compute final score
    7. Decide action
    8. Execute trade if BUY/SELL
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

    # ── 7. Decide Action ────────────────────────────────────────────────
    is_holding = await has_position(ticker)
    action = decide_action(
        final_score=final_score,
        gemini_action=gemini_action,
        crisis_detected=crisis_detected,
        risk_assessment=risk_assessment,
        has_position=is_holding,
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

    # ── 8. Execute ───────────────────────────────────────────────────────
    if action == TradeAction.BUY:
        max_pos_pct = min(
            gemini_result.get("position_size_pct", settings.max_position_pct),
            risk_assessment.max_allowed_position_pct,
        )
        trade = await execute_buy(
            ticker=ticker,
            price=current_price,
            analysis=analysis,
            max_position_pct=max_pos_pct,
        )
        if "error" not in trade:
            asyncio.create_task(send_trade_alert(trade))

    elif action == TradeAction.SELL:
        trade = await execute_sell(
            ticker=ticker,
            price=current_price,
            analysis=analysis,
        )
        if "error" not in trade:
            asyncio.create_task(send_trade_alert(trade))

    else:
        await log_hold(ticker, analysis)

    logger.info(
        f"[{action.value}] {ticker} @ Rs{current_price:.2f} | "
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
    }


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

        # Fetch live prices for all holdings
        live_prices = get_batch_prices(held_tickers)

        # Update valuation with live prices
        await update_portfolio_valuation(live_prices)

        total_sells = 0

        # ── 1. Stop-Loss / Trailing Stop (hard rules — execute immediately) ──
        stop_signals = check_stop_losses(portfolio, live_prices)

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

        underperformer_signals = detect_underperformers(portfolio, live_prices)

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
