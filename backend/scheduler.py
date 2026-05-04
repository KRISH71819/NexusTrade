"""
Scheduler — APScheduler cron job for the hourly analysis cycle.
Runs during Indian market hours (9:15 AM – 3:30 PM IST, Mon–Fri).
"""

import logging
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from config import settings
from data_ingestion import ingest_ticker_data, bulk_screener
from nifty_stocks import resolve_watchlist
from ml_engine import predict_trend
from llm_engine import analyze_sentiment
from execution_matrix import decide_action, build_action_reason
from ledger import execute_buy, execute_sell, log_hold, get_portfolio
from telegram_bot import send_trade_alert
from models import TradeAction, AnalysisResult

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def run_analysis_cycle():
    """
    The full analysis pipeline for all watchlist tickers:
    1. STAGE 1: Bulk Screen all tickers for momentum/volume
    2. STAGE 2: Deep Analysis (Ingest + ML + LLM) for top candidates
    3. Execute trade decision
    4. Send Telegram alert (if trade)
    """
    logger.info("═══ Starting Analysis Cycle ═══")
    
    # 1. Bulk Screen
    scan_universe = resolve_watchlist(settings.watchlist)
    top_candidates = bulk_screener(scan_universe, max_results=settings.max_candidates_for_ai)
    logger.info(f"Stage 1 Complete. Deep Analyzing top {len(top_candidates)} candidates: {top_candidates}")

    results = []

    for ticker in top_candidates:
        try:
            # 2. Ingest
            data = await ingest_ticker_data(ticker)
            if "error" in data:
                logger.warning(f"Skipping {ticker}: {data['error']}")
                continue

            current_price = data.get("latest_price")
            if not current_price:
                logger.warning(f"No price for {ticker}, skipping")
                continue

            indicators = data.get("indicators", {})
            news = data.get("news", [])
            headlines = [n.get("headline", "") for n in news[:3]]

            # 2. ML Prediction
            ml_result = await predict_trend(ticker, indicators)
            ml_confidence = ml_result["ml_confidence"]

            # 3. LLM Sentiment
            llm_result = await analyze_sentiment(ticker, headlines)
            sentiment_score = llm_result["sentiment_score"]
            explanation = llm_result["explanation"]

            # 4. Decision
            action = decide_action(ml_confidence, sentiment_score)
            reason = build_action_reason(action, ml_confidence, sentiment_score)

            analysis = AnalysisResult(
                ticker=ticker,
                current_price=current_price,
                ml_confidence=ml_confidence,
                ml_features_used=ml_result.get("features_used", {}),
                news_headlines=headlines,
                gemini_sentiment_score=sentiment_score,
                gemini_explanation=explanation,
                action=action,
                action_reason=reason,
            )

            # 5. Execute
            trade_doc = None
            if action == TradeAction.BUY:
                trade_doc = await execute_buy(ticker, current_price, analysis)
            elif action == TradeAction.SELL:
                trade_doc = await execute_sell(ticker, current_price, analysis)
            else:
                await log_hold(ticker, analysis)

            # 6. Telegram alert for trades
            if trade_doc and "error" not in trade_doc:
                await send_trade_alert(trade_doc)

            results.append({
                "ticker": ticker,
                "action": action.value,
                "price": current_price,
                "ml_confidence": ml_confidence,
                "sentiment": sentiment_score,
            })

            logger.info(
                f"{action.value} {ticker} @ ₹{current_price:.2f} | "
                f"ML: {ml_confidence:.2f} | Sentiment: {sentiment_score:+.2f}"
            )

        except Exception as e:
            logger.error(f"Error analyzing {ticker}: {e}", exc_info=True)

    logger.info(f"═══ Analysis Cycle Complete — {len(results)} tickers processed ═══")
    return results


def start_scheduler():
    """
    Start the APScheduler with a cron trigger for Indian market hours.
    Runs hourly from 9:15 AM to 3:15 PM IST (last run at 3:15, before 3:30 close).
    """
    trigger = CronTrigger(
        hour="9-15",
        minute="15",
        day_of_week="mon-fri",
        timezone=settings.scheduler_timezone,
    )

    scheduler.add_job(
        run_analysis_cycle,
        trigger=trigger,
        id="analysis_cycle",
        name="Hourly Analysis Cycle (NSE Market Hours)",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        f"Scheduler started — running hourly during "
        f"{settings.market_open_hour}:{settings.market_open_minute:02d}–"
        f"{settings.market_close_hour}:{settings.market_close_minute:02d} IST"
    )


def stop_scheduler():
    """Gracefully shut down the scheduler."""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped.")
