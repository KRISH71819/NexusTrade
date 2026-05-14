"""
Telegram Bot — sends trade alerts with full transparency.
"""

import httpx
import logging
from config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

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
            f"🤖 ML Confidence: {trade['ml_confidence']:.0%}\n"
            f"📰 Sentiment: {trade['gemini_sentiment_score']:+.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 [View Dashboard]({settings.dashboard_url})\n"
        )

        url = TELEGRAM_API.format(token=settings.telegram_bot_token)
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json={
                "chat_id": settings.telegram_chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            })

        if response.status_code == 200:
            logger.info(f"Telegram alert sent: {action} {trade['ticker']}")
            return True
        else:
            logger.error(
                f"Telegram API error {response.status_code}: {response.text[:200]}"
            )
            return False

    except httpx.TimeoutException:
        logger.error("Telegram alert timed out (10s)")
        return False
    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {type(e).__name__}: {e}")
        return False

