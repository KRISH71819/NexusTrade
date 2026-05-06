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
from risk_manager import assess_risk, check_stop_losses, compute_risk_adjustment
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


# ═══════════════════════════════════════════════════════════════════════════════
#   MAIN ANALYSIS CYCLE
# ═══════════════════════════════════════════════════════════════════════════════

async def run_analysis_cycle():
    """
    Full analysis cycle:
    1. Bulk screen the watchlist for momentum candidates
    2. Deep analyze top candidates (news + ML + Gemini + Risk)
    3. Execute BUY/SELL/HOLD decisions
    4. Update portfolio valuation with live prices
    """
    global _analysis_running
    if _analysis_running:
        logger.warning("Analysis cycle already running — skipping")
        return {"status": "skipped", "reason": "already_running"}

    _analysis_running = True
    cycle_start = datetime.now(timezone.utc)
    results = []

    try:
        logger.info("=" * 60)
        logger.info("ANALYSIS CYCLE START")
        logger.info("=" * 60)

        # ── Step 1: Get portfolio state ──────────────────────────────────
        portfolio = await get_portfolio()
        holdings = portfolio.get("holdings", [])
        held_tickers = [h["ticker"] for h in holdings if h.get("quantity", 0) > 0]

        # ── Step 2: Bulk screen watchlist ────────────────────────────────
        watchlist = resolve_watchlist(settings.watchlist)
        top_candidates = bulk_screener(watchlist, max_results=settings.max_candidates_for_ai)

        # Always include currently held stocks in the analysis
        analysis_tickers = list(set(top_candidates + held_tickers))
        logger.info(
            f"Analyzing {len(analysis_tickers)} tickers "
            f"({len(top_candidates)} screened + {len(held_tickers)} held)"
        )

        # ── Step 3: Deep analysis for each ticker ────────────────────────
        for ticker in analysis_tickers:
            try:
                result = await _analyze_single_ticker(ticker, portfolio)
                results.append(result)
            except Exception as e:
                logger.error(f"Error analyzing {ticker}: {e}", exc_info=True)
                results.append({"ticker": ticker, "error": str(e)})

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

        elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        logger.info(
            f"ANALYSIS CYCLE COMPLETE in {elapsed:.1f}s — "
            f"BUY: {actions['BUY']}, SELL: {actions['SELL']}, "
            f"HOLD: {actions['HOLD']}, ERRORS: {actions.get('ERROR', 0)}"
        )
        logger.info("=" * 60)

        return {
            "status": "completed",
            "tickers_analyzed": len(analysis_tickers),
            "actions": actions,
            "elapsed_seconds": round(elapsed, 1),
            "results": results,
        }

    except Exception as e:
        logger.error(f"Analysis cycle failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}

    finally:
        _analysis_running = False


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
    Quick risk scan: check all holdings for stop-loss and trailing stop triggers.
    This runs more frequently than the full analysis cycle.
    """
    try:
        portfolio = await get_portfolio()
        holdings = portfolio.get("holdings", [])

        if not holdings:
            return

        held_tickers = [h["ticker"] for h in holdings if h.get("quantity", 0) > 0]
        if not held_tickers:
            return

        # Fetch live prices
        live_prices = get_batch_prices(held_tickers)

        # Update valuation
        await update_portfolio_valuation(live_prices)

        # Check stop-losses
        sell_signals = check_stop_losses(portfolio, live_prices)

        for signal in sell_signals:
            ticker = signal["ticker"]
            logger.warning(f"RISK ALERT: {signal['reason']}")

            # Execute forced sell
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

        if sell_signals:
            logger.info(f"Risk check triggered {len(sell_signals)} forced sells")

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
