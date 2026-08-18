"""Quick test of the Phase 0 history engine (run after seeding)."""
import asyncio

from database import connect_db, close_db
from history_store import get_ohlcv, get_history_stats

TEST_TICKERS = ["RELIANCE.NS", "TCS.NS", "INFY.NS"]


async def main():
    await connect_db()
    print("=== History Stats ===")
    for ticker in TEST_TICKERS:
        stats = await get_history_stats(ticker)
        print(f"{ticker}: {stats['count']} rows | {stats['min_date']} -> {stats['max_date']}")

    print("\n=== Sample Read (RELIANCE.NS, last 5 bars) ===")
    df = await get_ohlcv("RELIANCE.NS")
    if df.empty:
        print("NO DATA — run fetch_history.py first")
    else:
        print(df.tail(5).to_string(index=False))

    await close_db()


asyncio.run(main())
