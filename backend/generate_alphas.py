"""
Offline CLI (Phases 2-4): LLM proposes alphas -> DSL validation -> CRITIC
pre-screen with revision loop -> sandbox backtest (clean window) -> registry
-> Hall of Fame promotion. NEVER run inside the HF live loop.

Usage (from backend/):
    python generate_alphas.py --count 4
    python generate_alphas.py --count 4 --generations 2
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

from config import settings
from database import connect_db, close_db
from alpha_generator import generate_candidates, revise_candidate
from alpha_critic import critique_candidate
from alpha_sandbox.dsl import validate_expression
from alpha_sandbox.sandbox import load_history_panel, backtest_signal
from alpha_sandbox.evaluator import compute_metrics, walk_forward_sharpes, apply_gates
from alpha_sandbox import registry, hall_of_fame

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("generate_alphas")

DEFAULT_TICKERS = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "TATAMOTORS.NS", "SUNPHARMA.NS", "LT.NS", "SBIN.NS", "BAJFINANCE.NS",
    "HINDALCO.NS", "MARUTI.NS",
]
DEFAULT_START = "2010-01-01"


async def process_candidate(cand: dict, panel: dict, bench_dd: float):
    """validate -> critique/revise loop -> backtest -> save. Returns metrics or None."""
    ok, err = validate_expression(cand["expression"])
    if not ok:
        print(f"  INVALID DSL — {err}")
        return None

    critique_trail = []
    for step in range(settings.alpha_max_revisions + 1):
        critique = critique_candidate(cand)
        critique_trail.append(critique)
        verdict = critique["verdict"]
        print(f"  CRITIC    : {verdict}"
              + (f" — {critique['fatal_flaw'] or '; '.join(critique['reasons'])}"
                 if verdict != "APPROVE" else ""))
        if verdict == "APPROVE":
            break
        if verdict == "REJECT" or step == settings.alpha_max_revisions:
            await registry.save_alpha_result(
                cand["expression"], f"llm_{cand['name']}",
                {"hypothesis": cand["hypothesis"], "critic": critique_trail,
                 "bench_max_dd_pct": bench_dd},
                {"days": 0}, [],
                {"sharpe": False, "max_dd": False, "stability": False, "all": False},
                source="llm",
            )
            return None
        revised = revise_candidate(cand, critique)
        if revised is None:
            print("  REVISION FAILED (no parseable output)")
            return None
        ok, err = validate_expression(revised["expression"])
        if not ok:
            print(f"  REVISED INVALID DSL — {err}")
            return None
        print(f"  REVISED   : {revised['expression']}")
        cand = revised

    daily_net, info = backtest_signal(
        panel, cand["expression"],
        cadence_days=int(cand.get("cadence_days", settings.alpha_default_cadence)),
        min_hold_days=int(cand.get("min_hold_days", settings.alpha_default_min_hold)),
    )
    if info.get("tickers_used", 0) == 0:
        print("  no usable tickers")
        return None
    metrics = compute_metrics(daily_net)
    metrics["ann_turnover"] = info.get("ann_turnover", 0.0)
    folds = walk_forward_sharpes(daily_net)
    gates = apply_gates(metrics, folds, bench_max_dd_pct=bench_dd)

    print(f"  tickers   : {info['tickers_used']} | days: {info['days']} | "
          f"exposure: {info['exposure_pct']}%")
    if metrics.get("status") == "insufficient_data":
        print(f"  NOT ENOUGH DATA ({metrics.get('days')} days)")
        return None
    print(f"  ann_return: {metrics['ann_return_pct']:+.2f}% | vol: {metrics['ann_vol_pct']:.2f}% | "
          f"Sharpe(net): {metrics['sharpe']:.2f}")
    print(f"  maxDD     : {metrics['max_dd_pct']:.2f}% | win(active): {metrics['win_rate_pct']:.1f}%")
    print(f"  turnover  : {metrics['ann_turnover']:.1f}x/yr (gate <= {settings.alpha_max_annual_turnover:.0f}x)")
    print(f"  fold Sharpes: {folds}")
    print(f"  GATE      : {'PASS' if gates['all'] else 'FAIL'} "
          f"(sharpe: {gates['sharpe']}, dd_vs_bench: {gates['max_dd']}, stable: {gates['stability']}, turnover: {gates['turnover']})")

    await registry.save_alpha_result(
        cand["expression"], f"llm_{cand['name']}",
        {"hypothesis": cand["hypothesis"], "critic": critique_trail,
         "bench_max_dd_pct": bench_dd, **info},
        metrics, folds, gates, source="llm",
    )
    return metrics


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--count", type=int, default=4)
    p.add_argument("--generations", type=int, default=1)
    p.add_argument("--tickers", type=str, default="")
    p.add_argument("--start", type=str, default=DEFAULT_START)
    args = p.parse_args()

    start_dt = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] or DEFAULT_TICKERS

    await connect_db()
    panel = await load_history_panel(tickers, start=start_dt)
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

    for gen in range(1, args.generations + 1):
        print(f"\n### GENERATION {gen}/{args.generations} ###")
        memory = await registry.list_alphas(limit=12)
        cands = generate_candidates(args.count, memory)
        if not cands:
            break
        best = None
        for cand in cands:
            print("\n" + "=" * 72)
            print(f"LLM ALPHA: {cand['name']}")
            print(f"  hypothesis: {cand['hypothesis'] or '(none)'}")
            print(f"  expr      : {cand['expression']}")
            m = await process_candidate(cand, panel, bench["max_dd_pct"])
            if m and m.get("sharpe") is not None and (best is None or m["sharpe"] > best):
                best = m["sharpe"]
        print(f"\nGeneration {gen} best Sharpe: {best if best is not None else 'n/a'}")

    hof = await hall_of_fame.refresh_hall_of_fame()
    print(f"\nHall of Fame: promoted={hof['promoted']} active={hof['active']}")
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
