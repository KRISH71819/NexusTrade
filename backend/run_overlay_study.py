"""
Phase 11 — Drawdown-control overlay study (OFFLINE ONLY).
Overlays: index 200d trend filter, vol targeting (15%), and both,
applied to the Phase-10 champions.
Gate: Sharpe >= 1.0 AND maxDD >= -25% AND all walk-forward folds > 0.
"""
import asyncio
import logging
import sys
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

from database import connect_db, close_db, get_db
from history_store import get_ohlcv
from alpha_sandbox.signal_library import build_signal_panels, close_panel
from alpha_sandbox.sandbox import backtest_ranking_from_signal
from alpha_sandbox.evaluator import compute_metrics, walk_forward_sharpes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("overlay_study")

CHAMPIONS = [
    ("mom_120", 10, 60),
    ("mom_120", 25, 60),
    ("trend_200", 25, 60),
]
MIN_BARS = 1500
START = datetime(2010, 1, 1, tzinfo=timezone.utc)
TARGET_VOL = 0.15


def composite_index(close_df: pd.DataFrame) -> pd.Series:
    norm = close_df.div(close_df.iloc[0])
    return norm.mean(axis=1, skipna=True)


def build_overlays(close_df: pd.DataFrame, base_net: pd.Series) -> dict:
    idx = composite_index(close_df)
    trend = (idx > idx.rolling(200, min_periods=200).mean()).fillna(False).astype(float)
    realized = base_net.rolling(60).std() * np.sqrt(252)
    volscale = (TARGET_VOL / realized.replace(0, np.nan)).clip(0.25, 1.0).fillna(1.0)
    return {
        "none": None,
        "trend200": trend,
        "vol15": volscale,
        "trend200+vol15": trend * volscale,
    }


def row(label: str, daily: pd.Series) -> str:
    m = compute_metrics(daily)
    folds = walk_forward_sharpes(daily)
    gate = (
        m["sharpe"] >= 1.0
        and m["max_dd_pct"] >= -25.0
        and len(folds) > 0
        and all(f > 0 for f in folds)
    )
    return (
        f"{label:<36s} ann {m['ann_return_pct']:>+7.2f}% vol {m['ann_vol_pct']:>5.2f}% "
        f"Sharpe {m['sharpe']:>5.2f} maxDD {m['max_dd_pct']:>7.2f}% "
        f"folds {folds} {'*** GATE PASS' if gate else ''}"
    )


async def _qualified_tickers(min_bars: int) -> list:
    coll = get_db()["ohlcv_history"]
    pipeline = [
        {"$group": {"_id": "$ticker", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gte": min_bars}}},
    ]
    try:
        cursor = await coll.aggregate(pipeline)
        docs = await cursor.to_list(length=1000) if hasattr(cursor, "to_list") else [d async for d in cursor]
    except TypeError:
        cursor = coll.aggregate(pipeline)
        docs = await cursor.to_list(length=1000) if hasattr(cursor, "to_list") else [d async for d in cursor]
    return sorted(d["_id"] for d in docs)


async def _load_panel_chunked(tickers: list, start_dt: datetime, chunk_size: int = 15) -> dict:
    panel = {}
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i : i + chunk_size]
        results = await asyncio.gather(*[get_ohlcv(t, start=start_dt) for t in chunk])
        for t, df in zip(chunk, results):
            if df is not None and not df.empty and len(df) >= 500:
                panel[t] = df
        logger.info(f"Loaded {min(i + chunk_size, len(tickers))}/{len(tickers)} tickers")
    return panel


async def main():
    await connect_db()
    tickers = await _qualified_tickers(MIN_BARS)
    logger.info(f"universe: {len(tickers)} tickers")

    panel = await _load_panel_chunked(tickers, START)
    logger.info(f"panel: {len(panel)} tickers")

    close_df = close_panel(panel)
    signal_panels = build_signal_panels(panel, [c[0] for c in CHAMPIONS])

    print("=" * 120)
    for sig, top_n, rb in CHAMPIONS:
        base_net, _ = backtest_ranking_from_signal(signal_panels[sig], close_df, top_n, rb)
        for name, exp in build_overlays(close_df, base_net).items():
            daily, _ = backtest_ranking_from_signal(
                signal_panels[sig], close_df, top_n, rb, exposure=exp
            )
            print(row(f"{sig} t{top_n}/{rb}d + {name}", daily))
        print("-" * 120)
    print("=" * 120)
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
