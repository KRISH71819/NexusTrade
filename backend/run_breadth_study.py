"""
Phase 10 — Breadth study (OFFLINE ONLY, never in the HF live loop).
Re-runs every ranking signal and the IC-learned blend over the seeded
NIFTY-500 history across diversification levels and cadences, so the
live configuration is chosen from evidence.

Usage (from backend/):
    python run_breadth_study.py
    python run_breadth_study.py --start 2010-01-01 --min-bars 1500
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from database import connect_db, close_db, get_db
from history_store import get_ohlcv
from alpha_sandbox.signal_library import build_signal_panels, close_panel
from alpha_sandbox.sandbox import backtest_ranking_from_signal
from alpha_sandbox.meta_scorer import run_meta_backtest
from alpha_sandbox.evaluator import compute_metrics, walk_forward_sharpes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("breadth_study")

TOP_NS = [10, 25, 50]
REBALANCES = [20, 60]


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


def _row(label: str, daily, info: dict) -> str:
    m = compute_metrics(daily)
    if m.get("status") == "insufficient_data":
        return f"{label:<34s} INSUFFICIENT DATA"
    folds = walk_forward_sharpes(daily)
    return (
        f"{label:<34s} ann {m['ann_return_pct']:>+7.2f}%  vol {m['ann_vol_pct']:>5.2f}%  "
        f"Sharpe {m['sharpe']:>5.2f}  maxDD {m['max_dd_pct']:>7.2f}%  "
        f"turn {info['ann_turnover']:>5.1f}x  folds {folds}"
    )


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
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=str, default="2010-01-01")
    p.add_argument("--min-bars", type=int, default=1500)
    args = p.parse_args()
    start_dt = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)

    await connect_db()
    tickers = await _qualified_tickers(args.min_bars)
    logger.info(f"Universe: {len(tickers)} tickers with >= {args.min_bars} bars since {args.start}")
    if len(tickers) < 100:
        logger.error("Seeding not far enough yet — let fetch_history.py --all progress and re-run.")
        await close_db()
        return

    panel = await _load_panel_chunked(tickers, start_dt)
    logger.info(f"panel: {len(panel)} tickers loaded")

    close_df = close_panel(panel)
    signal_panels = build_signal_panels(panel)   # one-time DSL evaluation
    logger.info(f"signals built: {list(signal_panels)}")

    print("=" * 110)
    print(f"BREADTH STUDY | universe={len(panel)} | window {args.start}+ | costs live-calibrated")
    print("=" * 110)

    for name, sdf in signal_panels.items():
        for tn in TOP_NS:
            for rb in REBALANCES:
                daily, info = backtest_ranking_from_signal(sdf, close_df, top_n=tn, rebalance_days=rb)
                print(_row(f"{name} top{tn}/{rb}d", daily, info))

    print("-" * 110)
    for tn in TOP_NS:
        for rb in REBALANCES:
            daily, info, _ = run_meta_backtest(
                signal_panels, close_df, top_n=tn, rebalance_days=rb, use_regime=False
            )
            print(_row(f"BLEND top{tn}/{rb}d", daily, info))

    print("=" * 110)
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
