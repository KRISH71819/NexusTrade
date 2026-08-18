"""
History Store — time-series OHLCV storage in MongoDB (Phase 0).

Solves the architecture's Achilles heel: `market_data` only keeps the LATEST
snapshot per ticker, so nothing could be backtested. This module adds a
dedicated `ohlcv_history` collection with one document per (ticker, trading
day), idempotent writes, and range reads that return pandas DataFrames ready
for the backtest sandbox (Phase 1+).

Rules:
- Unique index on (ticker, date) → re-seeding NEVER creates duplicates.
- Writes happen offline via fetch_history.py; the live HF loop never writes here.
- Reads are async, sorted by date ascending.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from pymongo.errors import BulkWriteError

from database import get_ohlcv_history_collection

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════
def _to_utc_day(ts) -> datetime:
    """Normalize any timestamp (pandas/numpy/datetime) to UTC midnight of its calendar date."""
    day = pd.Timestamp(ts).date()
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════
# WRITES (idempotent)
# ═══════════════════════════════════════════════════════════════════════════
async def append_ohlcv(ticker: str, df: pd.DataFrame) -> dict:
    """
    Idempotently insert daily OHLCV rows for one ticker.

    Rows whose (ticker, date) already exists are counted as skipped —
    not re-inserted, not errored. Returns {"inserted": int, "skipped": int}.
    """
    if df is None or df.empty:
        return {"inserted": 0, "skipped": 0}

    work = df.dropna(subset=["close"]).copy()
    if work.empty:
        return {"inserted": 0, "skipped": 0}

    time_col = "datetime" if "datetime" in work.columns else work.columns[0]

    docs = []
    for _, row in work.iterrows():
        vol = row.get("volume", 0)
        docs.append({
            "ticker": ticker,
            "date": _to_utc_day(row[time_col]),
            "open": float(row.get("open", 0.0)),
            "high": float(row.get("high", 0.0)),
            "low": float(row.get("low", 0.0)),
            "close": float(row["close"]),
            "volume": 0 if pd.isna(vol) else int(vol),
        })
    if not docs:
        return {"inserted": 0, "skipped": 0}

    coll = get_ohlcv_history_collection()
    try:
        result = await coll.insert_many(docs, ordered=False)
        inserted = len(result.inserted_ids)
    except BulkWriteError as bwe:
        # Duplicate-key (code 11000) = already stored → skipped.
        inserted = bwe.details.get("nInserted", 0)
        odd = [e for e in bwe.details.get("writeErrors", []) if e.get("code") != 11000]
        if odd:
            logger.error(f"{ticker}: {len(odd)} unexpected write errors: {odd[:3]}")

    skipped = len(docs) - inserted
    logger.info(f"{ticker}: history append inserted={inserted} skipped={skipped}")
    return {"inserted": inserted, "skipped": skipped}


# ═══════════════════════════════════════════════════════════════════════════
# READS
# ═══════════════════════════════════════════════════════════════════════════
async def get_ohlcv(
    ticker: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    Return stored daily bars for a ticker as DataFrame
    [date, open, high, low, close, volume], sorted ascending.
    """
    query: dict = {"ticker": ticker}
    date_q: dict = {}
    if start is not None:
        date_q["$gte"] = start
    if end is not None:
        date_q["$lte"] = end
    if date_q:
        query["date"] = date_q

    coll = get_ohlcv_history_collection()
    docs = await coll.find(query, {"_id": 0}).sort("date", 1).to_list(length=20_000)
    if not docs:
        return pd.DataFrame()
    df = pd.DataFrame(docs)
    return df[["date", "open", "high", "low", "close", "volume"]]


async def get_history_stats(ticker: str) -> dict:
    """Count + first/last stored date for a ticker (used for incremental seeding)."""
    coll = get_ohlcv_history_collection()
    count = await coll.count_documents({"ticker": ticker})
    if count == 0:
        return {"ticker": ticker, "count": 0, "min_date": None, "max_date": None}
    first = await coll.find_one({"ticker": ticker}, {"_id": 0, "date": 1}, sort=[("date", 1)])
    last = await coll.find_one({"ticker": ticker}, {"_id": 0, "date": 1}, sort=[("date", -1)])
    return {
        "ticker": ticker,
        "count": count,
        "min_date": first["date"].isoformat() if first else None,
        "max_date": last["date"].isoformat() if last else None,
    }


async def ensure_history_indexes() -> None:
    """Belt-and-braces index creation for offline scripts (database.py also does this at startup)."""
    coll = get_ohlcv_history_collection()
    await coll.create_index([("ticker", 1), ("date", 1)], unique=True)
    logger.info("ohlcv_history indexes ensured.")
