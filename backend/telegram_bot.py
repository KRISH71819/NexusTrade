"""
Telegram Bot — sends trade alerts with full transparency.
"""

import httpx
import logging
from config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _fmt_pct(value, missing: str = "N/A") -> str:
    """Format a 0-1 confidence as a percent, tolerating None (failure state)."""
    if value is None:
        return missing
    try:
        return f"{value:.0%}"
    except (TypeError, ValueError):
        return missing

# ── Startup validation ──────────────────────────────────────────────────────
if settings.telegram_bot_token and settings.telegram_chat_id:
    logger.info(
        f"Telegram configured — bot token: ...{settings.telegram_bot_token[-6:]}, "
        f"chat_id: {settings.telegram_chat_id}"
    )
else:
    logger.warning(
        "Telegram NOT configured — "
        f"bot_token={'SET' if settings.telegram_bot_token else 'MISSING'}, "
        f"chat_id={'SET' if settings.telegram_chat_id else 'MISSING'}"
    )


async def send_message(text: str, parse_mode: str | None = "Markdown") -> bool:
    """
    Send a plain text/markdown message to the configured Telegram chat.

    Used for system alerts (circuit breaker, LLM/ML failure storms, the daily
    one-page report) that are not per-trade notifications.
    """
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.debug("Telegram not configured — skipping message")
        return False

    url = TELEGRAM_API.format(token=settings.telegram_bot_token)
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
        if response.status_code == 200:
            return True
        logger.error(f"Telegram message error {response.status_code}: {response.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {type(e).__name__}: {e}")
        return False


async def send_trade_alert(trade: dict) -> bool:
    """
    Send a formatted Telegram message when a BUY or SELL is executed.

    Message includes: ticker, action, price, ML confidence,
    Gemini sentiment, and a link to the dashboard.
    """
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.debug("Telegram not configured — skipping alert")
        return False

    try:
        action = trade.get("action", "UNKNOWN")
        emoji = "🟢" if action == "BUY" else "🔴" if action == "SELL" else "⚪"

        message = (
            f"{emoji} *{action}* — {trade['ticker']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Price: Rs.{trade['price']:,.2f}\n"
            f"📦 Qty: {trade['quantity']}\n"
            f"💵 Total: Rs.{trade['total_value']:,.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 ML Confidence: {_fmt_pct(trade.get('ml_confidence'))}\n"
            f"📰 Sentiment: {trade['gemini_sentiment_score']:+.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 [View Dashboard]({settings.dashboard_url})\n"
        )

        url = TELEGRAM_API.format(token=settings.telegram_bot_token)
        payload = {
            "chat_id": settings.telegram_chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        # Retry once on timeout (Telegram API can be slow under load)
        import asyncio
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(url, json=payload)

                if response.status_code == 200:
                    logger.info(f"Telegram alert sent: {action} {trade['ticker']}")
                    return True
                elif response.status_code == 429:
                    # Rate limited by Telegram — wait and retry
                    retry_after = int(response.headers.get("Retry-After", "5"))
                    logger.warning(f"Telegram rate limited, retrying in {retry_after}s")
                    await asyncio.sleep(retry_after)
                    continue
                else:
                    logger.error(
                        f"Telegram API error {response.status_code}: {response.text[:200]}"
                    )
                    return False

            except httpx.TimeoutException:
                if attempt == 0:
                    logger.warning("Telegram alert timed out (30s), retrying in 2s...")
                    await asyncio.sleep(2)
                    continue
                logger.error("Telegram alert timed out after retry (30s)")
                return False

        return False

    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {type(e).__name__}: {e}")
        return False

