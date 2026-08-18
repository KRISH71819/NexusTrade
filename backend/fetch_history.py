"""
History Seeder (Phase 0) — offline yfinance → Mongo daily-bar loader.

Usage (run from backend/ on your laptop, NOT on HF Spaces):
    python fetch_history.py --tickers RELIANCE.NS,TCS.NS,INFY.NS
    python fetch_history.py --all                      # full resolved watchlist
    python fetch_history.py --period 5y

Properties:
- Idempotent: unique (ticker, date) index + dup-tolerant inserts.
- Resumable/incremental: if history exists, only bars after the last stored
  date are fetched.
- Rate-limit friendly: sleep + jitter between tickers.
- Never imported by the live app (main.py / scheduler.py do not touch this).
"""
import argparse
import asyncio
import logging
import random
import sys
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from config import settings
from database import connect_db, close_db
from history_store import append_ohlcv, ensure_history_indexes, get_history_stats
from nifty_stocks import resolve_watchlist

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("fetch_history")


# ═══════════════════════════════════════════════════════════════════════════
# YFINANCE PULL
# ═══════════════════════════════════════════════════════════════════════════
def _fetch_daily(ticker: str, period: str, start: str | None) -> pd.DataFrame:
    """Pull daily bars (auto-adjusted) from yfinance; lowercase columns."""
    t = yf.Ticker(ticker)
    if start:
        df = t.history(start=start, interval="1d", auto_adjust=True)
    else:
        df = t.history(period=period, interval="1d", auto_adjust=True)
    if df is None or df.empty:
        return pd.DataFrame()
    df.columns = [c.lower().replace(" ", "_") for c in df.columns]
    return df.reset_index()


async def seed_ticker(ticker: str, period: str) -> dict:
    """Seed one ticker; incremental if history already exists."""
    stats = await get_history_stats(ticker)

    start: str | None = None
    if stats["max_date"]:
        next_day = datetime.fromisoformat(stats["max_date"]) + timedelta(days=1)
        if next_day.date() > datetime.now(timezone.utc).date():
            return {"ticker": ticker, "status": "up_to_date", "count": stats["count"]}
        start = next_day.strftime("%Y-%m-%d")

    df = await asyncio.to_thread(_fetch_daily, ticker, period, start)
    if df.empty:
        status = "up_to_date" if start else "no_data"
        return {"ticker": ticker, "status": status, "inserted": 0, "skipped": 0}

    res = await append_ohlcv(ticker, df)
    return {"ticker": ticker, "status": "ok", **res}


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed ohlcv_history from yfinance (offline).")
    p.add_argument("--tickers", type=str, default="",
                   help="Comma-separated tickers, e.g. RELIANCE.NS,TCS.NS")
    p.add_argument("--all", action="store_true",
                   help="Seed the entire resolved watchlist (NIFTY500)")
    p.add_argument("--period", type=str, default="max",
                   help="yfinance period for first-time seed (default: max)")
    p.add_argument("--sleep", type=float, default=1.5,
                   help="Base sleep seconds between tickers (rate-limit safety)")
    return p.parse_args()


async def main() -> None:
    args = _parse_args()

    if args.all:
        tickers = resolve_watchlist(settings.watchlist)
    else:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        logger.error("No tickers given. Use --tickers A.NS,B.NS or --all")
        return

    logger.info(f"Seeding {len(tickers)} ticker(s) | period={args.period}")
    if len(tickers) > 100:
        logger.warning(
            f"Large seed: ~{len(tickers)} tickers takes a while (yfinance rate limits). "
            "Run in the background; re-run later to resume — it is incremental."
        )

    await connect_db()
    await ensure_history_indexes()

    totals = {"ok": 0, "up_to_date": 0, "no_data": 0, "error": 0, "inserted": 0}
    for i, ticker in enumerate(tickers, 1):
        t0 = time.monotonic()
        try:
            res = await seed_ticker(ticker, args.period)
        except Exception as e:
            logger.error(f"[{i}/{len(tickers)}] {ticker} FAILED: {e}")
            res = {"ticker": ticker, "status": "error"}

        totals[res.get("status", "error")] = totals.get(res.get("status", "error"), 0) + 1
        totals["inserted"] += res.get("inserted", 0)
        logger.info(f"[{i}/{len(tickers)}] {ticker} -> {res} ({time.monotonic() - t0:.1f}s)")

        if i < len(tickers):
            await asyncio.sleep(args.sleep + random.uniform(0.0, 0.75))

    await close_db()
    logger.info(f"DONE: {totals}")


if __name__ == "__main__":
    asyncio.run(main())
