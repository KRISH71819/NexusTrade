"""
Offline alpha backtest CLI (Phase 1). NEVER run inside the HF live loop.

Usage (from backend/):
    python run_backtest.py                     # 3 classic alphas on 12 default tickers
    python run_backtest.py --tickers RELIANCE.NS,TCS.NS --expr "close / sma(close, 20) - 1" --name my_alpha
    python run_backtest.py --start 2010-01-01
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config import settings
from database import connect_db, close_db
from alpha_sandbox import registry
from alpha_sandbox.dsl import validate_expression
from alpha_sandbox.sandbox import load_history_panel, backtest_signal, backtest_ranking
from alpha_sandbox.evaluator import compute_metrics, walk_forward_sharpes, apply_gates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("run_backtest")

DEFAULT_TICKERS = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "TATAMOTORS.NS", "SUNPHARMA.NS", "LT.NS", "SBIN.NS", "BAJFINANCE.NS",
    "HINDALCO.NS", "MARUTI.NS",
]

CLASSIC_ALPHAS = {
    "momentum_20": "close / sma(close, 20) - 1",
    "meanrev_5": "-zscore(close, 5)",
    "volume_momentum_20": "(close / sma(close, 20) - 1) * volume_ratio(volume, 20)",
}

RANKING_ALPHAS = {
    "rank_momentum_20": "close / sma(close, 20) - 1",
    "rank_momentum_60": "close / sma(close, 60) - 1",
    "rank_reversal_5": "-zscore(close, 5)",
    "rank_vol_momentum_60": "(close / sma(close, 60) - 1) * volume_ratio(volume, 20)",
}


async def run_one(name: str, expression: str, panel: dict, bench_dd: float | None = None):
    ok, err = validate_expression(expression)
    if not ok:
        logger.error(f"{name}: INVALID expression — {err}")
        return
    daily_net, info = backtest_signal(panel, expression)
    if info.get("tickers_used", 0) == 0:
        logger.error(f"{name}: no usable tickers")
        return
    metrics = compute_metrics(daily_net)
    metrics["ann_turnover"] = info.get("ann_turnover", 0.0)
    fold_sharpes = walk_forward_sharpes(daily_net)
    gates = apply_gates(metrics, fold_sharpes, bench_max_dd_pct=bench_dd)

    print("\n" + "=" * 72)
    print(f"ALPHA: {name}")
    print(f"  expr      : {expression}")
    print(f"  tickers   : {info['tickers_used']} | days: {info['days']} | "
          f"exposure: {info['exposure_pct']}% | cost/side: {info['one_side_cost_rate']:.4%}")
    if metrics.get("status") == "insufficient_data":
        print(f"  NOT ENOUGH DATA ({metrics.get('days')} days)")
        return
    print(f"  ann_return: {metrics['ann_return_pct']:+.2f}% | vol: {metrics['ann_vol_pct']:.2f}% | "
          f"Sharpe(net): {metrics['sharpe']:.2f}")
    print(f"  maxDD     : {metrics['max_dd_pct']:.2f}% | win(active days): {metrics['win_rate_pct']:.1f}%")
    print(f"  turnover  : {metrics['ann_turnover']:.1f}x/yr (gate <= {settings.alpha_max_annual_turnover:.0f}x)")
    print(f"  fold Sharpes: {fold_sharpes}")
    print(f"  GATE      : {'PASS' if gates['all'] else 'FAIL'} "
          f"(sharpe: {gates['sharpe']}, dd_vs_bench: {gates['max_dd']}, stable: {gates['stability']}, turnover: {gates['turnover']})")
    info["bench_max_dd_pct"] = bench_dd
    await registry.save_alpha_result(expression, name, info, metrics, fold_sharpes, gates)


async def run_ranking(
    name: str, expression: str, panel: dict, bench_dd: float | None = None,
    top_n: int = 10, rebalance: int = 20
):
    ok, err = validate_expression(expression)
    if not ok:
        logger.error(f"{name}: INVALID expression — {err}")
        return
    daily_net, info = backtest_ranking(panel, expression, top_n=top_n, rebalance_days=rebalance)
    if info.get("tickers_used", 0) == 0:
        logger.error(f"{name}: no usable tickers")
        return
    metrics = compute_metrics(daily_net)
    metrics["ann_turnover"] = info.get("ann_turnover", 0.0)
    fold_sharpes = walk_forward_sharpes(daily_net)
    gates = apply_gates(metrics, fold_sharpes, bench_max_dd_pct=bench_dd)

    print("\n" + "=" * 72)
    print(f"RANKING ALPHA: {name}")
    print(f"  expr      : {expression}")
    print(f"  tickers   : {info['tickers_used']} | days: {info['days']} | "
          f"top_n: {info.get('top_n', top_n)} | rebalance: {info.get('rebalance_days', rebalance)}d | "
          f"exposure: {info['exposure_pct']}% | cost/side: {info['one_side_cost_rate']:.4%}")
    if metrics.get("status") == "insufficient_data":
        print(f"  NOT ENOUGH DATA ({metrics.get('days')} days)")
        return
    print(f"  ann_return: {metrics['ann_return_pct']:+.2f}% | vol: {metrics['ann_vol_pct']:.2f}% | "
          f"Sharpe(net): {metrics['sharpe']:.2f}")
    print(f"  maxDD     : {metrics['max_dd_pct']:.2f}% | win(active days): {metrics['win_rate_pct']:.1f}%")
    print(f"  turnover  : {metrics['ann_turnover']:.1f}x/yr (gate <= {settings.alpha_max_annual_turnover:.0f}x)")
    print(f"  fold Sharpes: {fold_sharpes}")
    print(f"  GATE      : {'PASS' if gates['all'] else 'FAIL'} "
          f"(sharpe: {gates['sharpe']}, dd_vs_bench: {gates['max_dd']}, stable: {gates['stability']}, turnover: {gates['turnover']})")
    info["bench_max_dd_pct"] = bench_dd
    await registry.save_alpha_result(expression, name, info, metrics, fold_sharpes, gates, source="classic_rank")


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", type=str, default="")
    p.add_argument("--expr", type=str, default="")
    p.add_argument("--name", type=str, default="custom_alpha")
    p.add_argument("--start", type=str, default="2010-01-01")
    p.add_argument("--mode", choices=["timing", "ranking"], default="timing")
    p.add_argument("--top-n", type=int, default=10)
    p.add_argument("--rebalance", type=int, default=20)
    args = p.parse_args()

    start_dt = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] or DEFAULT_TICKERS

    await connect_db()
    panel = await load_history_panel(tickers, start=start_dt)
    logger.info(f"panel loaded: {len(panel)}/{len(tickers)} tickers (start={args.start})")
    if not panel:
        logger.error("No history loaded. Run fetch_history.py first (Phase 0).")
        await close_db()
        return

    bench_daily, _ = backtest_signal(panel, "close / close")
    bench = compute_metrics(bench_daily)
    print("=" * 72)
    print(f"BENCHMARK (equal-weight buy&hold, {args.start}+): "
          f"ann {bench['ann_return_pct']:+.2f}% | Sharpe {bench['sharpe']:.2f} | "
          f"maxDD {bench['max_dd_pct']:.2f}%")
    print("=" * 72)

    if args.mode == "ranking":
        if args.expr:
            await run_ranking(args.name, args.expr, panel, bench_dd=bench["max_dd_pct"],
                              top_n=args.top_n, rebalance=args.rebalance)
        else:
            for name, expr in RANKING_ALPHAS.items():
                await run_ranking(name, expr, panel, bench_dd=bench["max_dd_pct"],
                                  top_n=args.top_n, rebalance=args.rebalance)
    else:
        if args.expr:
            await run_one(args.name, args.expr, panel, bench_dd=bench["max_dd_pct"])
        else:
            for name, expr in CLASSIC_ALPHAS.items():
                await run_one(name, expr, panel, bench_dd=bench["max_dd_pct"])

    approved = await registry.list_alphas(limit=5, status="approved")
    print(f"\nalpha_registry: {len(approved)} approved alpha(s) among last 5 results")
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
