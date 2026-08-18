"""
Offline CLI (Phase 7): meta-scorer blend, with and without regime overlay.
NEVER run inside the HF live loop.

Usage (from backend/):
    python run_meta_scorer.py --tickers <same 38-ticker list>
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

from database import connect_db, close_db
from alpha_sandbox.sandbox import load_history_panel, backtest_signal
from alpha_sandbox.evaluator import compute_metrics, walk_forward_sharpes, apply_gates
from alpha_sandbox.signal_library import build_signal_panels, close_panel
from alpha_sandbox.meta_scorer import run_meta_backtest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("run_meta_scorer")

DEFAULT_TICKERS = [
    "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "AXISBANK.NS",
    "KOTAKBANK.NS", "BAJFINANCE.NS", "TCS.NS", "INFY.NS", "HCLTECH.NS",
    "WIPRO.NS", "TECHM.NS", "SUNPHARMA.NS", "CIPLA.NS", "DRREDDY.NS",
    "MARUTI.NS", "TATAMOTORS.NS", "EICHERMOT.NS", "LT.NS", "ULTRACEMCO.NS",
    "GRASIM.NS", "HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "TITAN.NS",
    "ASIANPAINT.NS", "BHARTIARTL.NS", "ONGC.NS", "NTPC.NS", "COALINDIA.NS",
    "TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "VEDL.NS", "POWERGRID.NS",
    "TRENT.NS", "DMART.NS", "PIDILITIND.NS", "NAUKRI.NS", "ZOMATO.NS",
]


def print_block(label, daily, bench_dd, ann_turnover=0.0):
    metrics = compute_metrics(daily)
    metrics["ann_turnover"] = ann_turnover
    folds = walk_forward_sharpes(daily)
    gates = apply_gates(metrics, folds, bench_max_dd_pct=bench_dd)
    print("\n" + "=" * 72)
    print(f"META-SCORER: {label}")
    if metrics.get("status") == "insufficient_data":
        print("  NOT ENOUGH DATA")
        return metrics
    print(f"  ann_return: {metrics['ann_return_pct']:+.2f}% | vol: {metrics['ann_vol_pct']:.2f}% | "
          f"Sharpe(net): {metrics['sharpe']:.2f}")
    print(f"  maxDD     : {metrics['max_dd_pct']:.2f}% | win(active): {metrics['win_rate_pct']:.1f}%")
    print(f"  fold Sharpes: {folds}")
    print(f"  GATE      : {'PASS' if gates['all'] else 'FAIL'} "
          f"(sharpe: {gates['sharpe']}, dd_vs_bench: {gates['max_dd']}, "
          f"stable: {gates['stability']}, turnover: {gates['turnover']})")
    return metrics


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", type=str, default="")
    p.add_argument("--top-n", type=int, default=10)
    p.add_argument("--rebalance", type=int, default=20)
    p.add_argument("--start", type=str, default="2010-01-01")
    args = p.parse_args()

    start_dt = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] or DEFAULT_TICKERS

    await connect_db()
    panel = await load_history_panel(tickers, start=start_dt)
    if not panel:
        logger.error("No history loaded.")
        await close_db()
        return

    close_df = close_panel(panel)
    signal_panels = build_signal_panels(panel)
    logger.info(f"panel: {close_df.shape}, signals: {list(signal_panels)}")

    bench_daily, _ = backtest_signal(panel, "close / close")
    bench = compute_metrics(bench_daily)
    print("=" * 72)
    print(f"BENCHMARK (equal-weight buy&hold, {args.start}+): "
          f"ann {bench['ann_return_pct']:+.2f}% | Sharpe {bench['sharpe']:.2f} | "
          f"maxDD {bench['max_dd_pct']:.2f}%")

    for use_regime in (False, True):
        daily, info, wrows = run_meta_backtest(
            signal_panels, close_df,
            top_n=args.top_n, rebalance_days=args.rebalance,
            use_regime=use_regime,
        )
        label = "META+REGIME" if use_regime else "META (no regime)"
        print(f"\n  exposure: {info['exposure_pct']}% | turnover: {info['ann_turnover']}x/yr")
        print_block(label, daily, bench["max_dd_pct"], ann_turnover=info["ann_turnover"])
        last = wrows[-1]
        print(f"  latest weights : {last['weights']}")
        print(f"  latest raw ICs : {last['raw_ic']}")
        print(f"  regime bull    : {last['regime_bull']} | picks: {last['n_picks']}")

    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
