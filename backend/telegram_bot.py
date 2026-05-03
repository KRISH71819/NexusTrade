"""
Telegram Bot — sends trade alerts with full transparency.
Phase 2: Will send real messages after user provides bot token.
"""

import httpx
import logging
from config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def send_trade_alert(trade: dict) -> bool:
    """
    Send a formatted Telegram message when a BUY or SELL is executed.

    Message includes: ticker, action, price, ML confidence,
    Gemini sentiment, and a link to the dashboard.
    """
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("Telegram not configured — skipping alert")
        return False

    try:
        action = trade.get("action", "UNKNOWN")
        emoji = "🟢" if action == "BUY" else "🔴" if action == "SELL" else "⚪"

        message = (
            f"{emoji} *{action}* — {trade['ticker']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Price: ₹{trade['price']:,.2f}\n"
            f"📦 Qty: {trade['quantity']}\n"
            f"💵 Total: ₹{trade['total_value']:,.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 ML Confidence: {trade['ml_confidence']:.0%}\n"
            f"📰 Sentiment: {trade['gemini_sentiment_score']:+.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 [View Dashboard]({settings.dashboard_url})\n"
        )

        url = TELEGRAM_API.format(token=settings.telegram_bot_token)
        async with httpx.AsyncClient() as client:
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
            logger.error(f"Telegram API error: {response.status_code} — {response.text}")
            return False

    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {e}")
        return False
