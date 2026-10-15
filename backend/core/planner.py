"""
Rebalance Planner, MTM Engine & Scheduler Hooks (v2 Phase 4).
Strictly End-of-Day (EOD) portfolio engine:
- Catastrophic Stops (-25% from avg cost)
- Daily MTM & Regime Classification (15:32 IST)
- Rebalance Planner (15:40 IST) with 60-day Cadence Gate, Rank Buffers (25/35),
  +-10% No-Trade Band, Cost-Aware Swap Skip, 30% Turnover Budget Scale-Down.
- Kill Switch Gate (@require_v2_active).
"""

from dataclasses import dataclass
from datetime import datetime, date, timezone
import functools
import logging
from typing import Dict, Any, List, Optional, Tuple, Set

from core.book import Book
from core.ist import ist_today_str
from core.regime import classify_regime, PHASE4_CAPS

logger = logging.getLogger(__name__)


@dataclass
class TradeIntent:
    ticker: str
    side: str  # "BUY" or "SELL"
    quantity: int
    price: float
    reason: str = ""
    rank: Optional[int] = None
    target_weight: Optional[float] = None
    current_weight: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "reason": self.reason,
            "rank": self.rank,
            "target_weight": self.target_weight,
            "current_weight": self.current_weight,
        }


def should_rebalance(
    last_str: Optional[str],
    today_str: str,
    cadence_days: int = 60,
    regime: str = "NEUTRAL",
    prev_regime: Optional[str] = None,
) -> bool:
    """Rebalance occurs if >= cadence_days (trading days) passed OR if regime shifted to/from CRISIS."""
    if not last_str:
        return True

    # Immediate rebalance on shift into or out of CRISIS
    if prev_regime is not None and prev_regime != regime:
        if regime == "CRISIS" or prev_regime == "CRISIS":
            return True

    try:
        last = date.fromisoformat(last_str)
        today = date.fromisoformat(today_str)
    except ValueError:
        return True

    calendar_cadence = cadence_days * 7 // 5
    return (today - last).days >= calendar_cadence


def check_catastrophic_stops(
    holdings: Dict[str, Dict[str, Any]] | List[Dict[str, Any]],
    prices: Dict[str, float],
    stop_pct: float = 0.25,
) -> List[TradeIntent]:
    """Flag holdings where current_price <= avg_cost * (1 - stop_pct) for immediate MOC exit."""
    if isinstance(holdings, dict):
        h_list = list(holdings.values())
    else:
        h_list = holdings

    intents = []
    for h in h_list:
        ticker = h.get("ticker")
        qty = h.get("quantity", 0)
        avg = h.get("avg_price", 0.0)
        if not ticker or qty <= 0 or avg <= 0:
            continue
        cur_p = prices.get(ticker, h.get("current_price", 0.0))
        if cur_p > 0 and cur_p <= avg * (1.0 - stop_pct):
            intents.append(
                TradeIntent(
                    ticker=ticker,
                    side="SELL",
                    quantity=qty,
                    price=cur_p,
                    reason="catastrophic_stop_25pct",
                    rank=h.get("rank"),
                )
            )
    return intents


def swap_worth_it(rank_a: int, rank_b: int, min_delta: int = 5) -> bool:
    """Cost-aware swap check: delta rank < 5 => skip the swap."""
    return abs(rank_a - rank_b) >= min_delta


def apply_turnover_budget_scale_down(
    intents: List[TradeIntent],
    total_equity: float,
    turnover_limit: float = 0.30,
    scale_down_pct: float = 0.30,
) -> Tuple[List[TradeIntent], bool]:
    """If gross turnover exceeds turnover_limit, scale down all non-mandatory drift adjustments by scale_down_pct."""
    if total_equity <= 0:
        return intents, False

    gross_turnover = sum(intent.quantity * intent.price for intent in intents) / total_equity
    if gross_turnover <= turnover_limit:
        return intents, False

    adjusted_intents = []
    for it in intents:
        # Mandatory trades: catastrophic stop, buffer exit, crisis liquidation, new entries
        is_mandatory = (
            "catastrophic" in it.reason
            or "exit" in it.reason
            or "crisis" in it.reason
            or "new_entry" in it.reason
        )
        if is_mandatory:
            adjusted_intents.append(it)
        else:
            # Non-mandatory drift correction: scale down adjustment by 30%
            new_qty = int(it.quantity * (1.0 - scale_down_pct))
            if new_qty >= 1:
                adjusted_intents.append(
                    TradeIntent(
                        ticker=it.ticker,
                        side=it.side,
                        quantity=new_qty,
                        price=it.price,
                        reason=f"{it.reason}_scaled_{int(scale_down_pct * 100)}pct",
                        rank=it.rank,
                        target_weight=it.target_weight,
                        current_weight=it.current_weight,
                    )
                )
    return adjusted_intents, True


def plan_rebalance(
    book: Book,
    ranked_universe: List[Any],
    prices: Dict[str, float],
    regime: str = "NEUTRAL",
    target_exposure: Optional[float] = None,
    top_n: int = 25,
    exit_n: int = 35,
    last_rebalance_date: Optional[str] = None,
    today_str: Optional[str] = None,
    prev_regime: Optional[str] = None,
    band: float = 0.10,
    turnover_limit: float = 0.30,
    min_swap_rank_delta: int = 5,
) -> List[TradeIntent]:
    """Generates target portfolio and TradeIntents with all Phase 4 execution constraints."""
    # 1. Cadence Gate
    if last_rebalance_date and today_str:
        if not should_rebalance(
            last_rebalance_date,
            today_str,
            cadence_days=60,
            regime=regime,
            prev_regime=prev_regime,
        ):
            return []

    # 2. Target exposure from regime
    if target_exposure is None:
        target_exposure = PHASE4_CAPS.get(regime, 0.80)

    # 3. Crisis liquidation (target exposure == 0)
    if regime == "CRISIS" or target_exposure <= 0.0:
        sells = []
        for ticker, h in book.holdings.items():
            qty = h.get("quantity", 0)
            if qty > 0:
                p = prices.get(ticker, h.get("current_price", h.get("avg_price", 0.0)))
                sells.append(
                    TradeIntent(
                        ticker=ticker,
                        side="SELL",
                        quantity=qty,
                        price=p,
                        reason="crisis_liquidation",
                        target_weight=0.0,
                        current_weight=1.0,
                    )
                )
        return sells

    # 4. Catastrophic Stops (pre-emptively exit)
    cat_intents = check_catastrophic_stops(book.holdings, prices, stop_pct=0.25)
    cat_tickers = {ci.ticker for ci in cat_intents}

    # Extract ranked ticker list and map
    if ranked_universe and isinstance(ranked_universe[0], (tuple, list)):
        ranked_tickers = [item[0] for item in ranked_universe]
    else:
        ranked_tickers = list(ranked_universe)
    rank_map = {t: idx for idx, t in enumerate(ranked_tickers, start=1)}

    held_active = {
        t: h for t, h in book.holdings.items()
        if h.get("quantity", 0) > 0 and t not in cat_tickers
    }

    # 5. Selection with Buffers:
    # Retain current holdings if Rank <= exit_n (35)
    retained_held: List[str] = []
    exit_tickers: List[str] = []
    for t in held_active.keys():
        r = rank_map.get(t, 9999)
        if r <= exit_n:
            retained_held.append(t)
        else:
            exit_tickers.append(t)

    # Candidate entries: only consider Rank <= top_n (25)
    entry_candidates = [
        t for t in ranked_tickers
        if rank_map.get(t, 9999) <= top_n and t not in held_active
    ]

    # 6. Cost-Aware Swap Skip:
    # If an exit was prompted to buy a candidate and delta rank < min_swap_rank_delta (5), skip the swap.
    if exit_tickers and entry_candidates:
        filtered_exits = []
        skipped_cands: Set[str] = set()
        for et in exit_tickers:
            r_exit = rank_map.get(et, 9999)
            should_skip = False
            matched_cand = None
            for cand in entry_candidates:
                if cand in skipped_cands:
                    continue
                r_cand = rank_map.get(cand, 9999)
                if abs(r_exit - r_cand) < min_swap_rank_delta:
                    should_skip = True
                    matched_cand = cand
                    break
            if should_skip and matched_cand:
                skipped_cands.add(matched_cand)
                retained_held.append(et)
            else:
                filtered_exits.append(et)
        exit_tickers = filtered_exits
        entry_candidates = [c for c in entry_candidates if c not in skipped_cands]

    # Target pool: retained holdings + new entries up to top_n
    target_tickers = list(retained_held)
    for cand in entry_candidates:
        if len(target_tickers) >= top_n:
            break
        target_tickers.append(cand)



    # 7. Total equity calculation
    holdings_val = sum(
        h.get("quantity", 0) * prices.get(t, h.get("current_price", h.get("avg_price", 0.0)))
        for t, h in book.holdings.items()
    )
    total_equity = book.cash + holdings_val
    if total_equity <= 0:
        return cat_intents

    # Target weights
    num_targets = len(target_tickers)
    target_weight_per_stock = (target_exposure / num_targets) if num_targets > 0 else 0.0
    target_val_per_stock = total_equity * target_weight_per_stock

    sells: List[TradeIntent] = list(cat_intents)
    buys: List[TradeIntent] = []
    drift_adjustments: List[TradeIntent] = []

    # Mandatory exits for stocks dropped from portfolio
    for et in exit_tickers:
        h = held_active.get(et)
        if h:
            qty = h.get("quantity", 0)
            p = prices.get(et, h.get("current_price", h.get("avg_price", 0.0)))
            cur_val = qty * p
            cur_w = cur_val / total_equity if total_equity > 0 else 0.0
            if qty > 0 and p > 0:
                sells.append(
                    TradeIntent(
                        ticker=et,
                        side="SELL",
                        quantity=qty,
                        price=p,
                        reason="buffer_exit_rank_gt_35",
                        rank=rank_map.get(et),
                        target_weight=0.0,
                        current_weight=round(cur_w, 4),
                    )
                )

    # Calculate trades for target pool
    for t in target_tickers:
        p = prices.get(t, 0.0)
        if p <= 0:
            continue

        h = held_active.get(t)
        cur_qty = h.get("quantity", 0) if h else 0
        cur_val = cur_qty * p
        cur_w = cur_val / total_equity if total_equity > 0 else 0.0

        if cur_qty == 0:
            # New Entry
            needed_val = target_val_per_stock
            needed_qty = int(needed_val // p)
            if needed_qty >= 1:
                buys.append(
                    TradeIntent(
                        ticker=t,
                        side="BUY",
                        quantity=needed_qty,
                        price=p,
                        reason="new_entry_rank_le_25",
                        rank=rank_map.get(t),
                        target_weight=round(target_weight_per_stock, 4),
                        current_weight=0.0,
                    )
                )
        else:
            # Already held: check +-10% no-trade band
            # Rule: only trade if |W_curr - W_target| > 10% of W_target
            w_diff = abs(cur_w - target_weight_per_stock)
            band_tolerance = band * target_weight_per_stock
            if w_diff <= band_tolerance:
                # Inside no-trade band -> skip drift adjustment
                continue

            if cur_val < target_val_per_stock:
                # Under-weight: drift BUY
                qty_to_buy = int((target_val_per_stock - cur_val) // p)
                if qty_to_buy >= 1:
                    drift_adjustments.append(
                        TradeIntent(
                            ticker=t,
                            side="BUY",
                            quantity=qty_to_buy,
                            price=p,
                            reason="drift_rebalance_buy",
                            rank=rank_map.get(t),
                            target_weight=round(target_weight_per_stock, 4),
                            current_weight=round(cur_w, 4),
                        )
                    )
            elif cur_val > target_val_per_stock:
                # Over-weight: drift trim SELL
                qty_to_sell = int((cur_val - target_val_per_stock) // p)
                qty_to_sell = min(qty_to_sell, cur_qty)
                if qty_to_sell >= 1:
                    drift_adjustments.append(
                        TradeIntent(
                            ticker=t,
                            side="SELL",
                            quantity=qty_to_sell,
                            price=p,
                            reason="drift_rebalance_trim",
                            rank=rank_map.get(t),
                            target_weight=round(target_weight_per_stock, 4),
                            current_weight=round(cur_w, 4),
                        )
                    )

    # 8. Turnover Budget Scale-Down
    combined_intents = sells + buys + drift_adjustments
    final_intents, hit_budget = apply_turnover_budget_scale_down(
        combined_intents,
        total_equity=total_equity,
        turnover_limit=turnover_limit,
        scale_down_pct=0.30,
    )

    # 9. Cash Reserve Guard: Ensure total BUY turnover never exceeds available cash
    # Sells yield proceeds, buys consume cash.
    estimated_proceeds = sum(
        it.quantity * it.price for it in final_intents if it.side == "SELL"
    )
    available_cash = book.cash + estimated_proceeds

    guarded_intents: List[TradeIntent] = []
    # Place sells first to free cash
    for it in final_intents:
        if it.side == "SELL":
            guarded_intents.append(it)

    if available_cash <= 0:
        # Cash reserve guard: no buys allowed if no cash
        return guarded_intents

    remaining_cash = available_cash
    for it in final_intents:
        if it.side == "BUY":
            cost = it.quantity * it.price
            if cost <= remaining_cash:
                guarded_intents.append(it)
                remaining_cash -= cost
            else:
                affordable_qty = int(remaining_cash // it.price)
                if affordable_qty >= 1:
                    guarded_intents.append(
                        TradeIntent(
                            ticker=it.ticker,
                            side="BUY",
                            quantity=affordable_qty,
                            price=it.price,
                            reason=f"{it.reason}_cash_capped",
                            rank=it.rank,
                            target_weight=it.target_weight,
                            current_weight=it.current_weight,
                        )
                    )
                    remaining_cash -= affordable_qty * it.price

    return guarded_intents


def require_v2_active(job_type: str = "rebalance"):
    """Gate V2 jobs against system_state_v2 kill switch.

    If kill_switch_enabled is True:
    - MTM ('mtm') still runs to preserve accurate equity curves and catastrophic stop monitoring.
    - Rebalance Planner ('rebalance') immediately aborts and logs a skip event.
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            db = kwargs.get("db")
            if db is None:
                try:
                    from database import get_db
                    db = get_db()
                except Exception:
                    db = None

            kill_switch = False
            if db is not None:
                try:
                    state_doc = await db["system_state_v2"].find_one({"_id": "v2_state"})
                    if state_doc:
                        kill_switch = bool(state_doc.get("kill_switch_enabled", False))
                except Exception as e:
                    logger.warning(f"Failed to check system_state_v2 kill switch: {e}")

            if kill_switch:
                if job_type == "rebalance":
                    logger.warning("V2 Kill Switch ACTIVE: Rebalance Planner aborted. No trades generated.")
                    return {"status": "aborted", "reason": "kill_switch_active", "trades": [], "intents": []}
                else:
                    logger.info("V2 Kill Switch ACTIVE: MTM running in monitoring-only mode.")

            return await func(*args, **kwargs)
        return wrapper
    return decorator


@require_v2_active(job_type="mtm")
async def v2_daily_mtm_and_regime(
    db=None,
    date_str: Optional[str] = None,
    prices: Optional[Dict[str, float]] = None,
    breadth_pct: Optional[float] = None,
    index_gap_pct: Optional[float] = None,
    vol_percentile: Optional[float] = None,
) -> Dict[str, Any]:
    """15:32 IST Job: Catastrophic stops check, MTM update to book_equity, regime log."""
    if db is None:
        from database import get_db
        db = get_db()

    date_str = date_str or ist_today_str()
    book = await Book.load(db)

    # 1. Prices fallback
    if prices is None:
        prices = {}
        for ticker, h in book.holdings.items():
            prices[ticker] = h.get("current_price", h.get("avg_price", 0.0))

    # 2. Catastrophic stop check (executed before regime calculation)
    cat_intents = check_catastrophic_stops(book.holdings, prices, stop_pct=0.25)
    executed_trades = []
    for ci in cat_intents:
        trade_record = book.apply_trade(
            ticker=ci.ticker,
            side=ci.side,
            qty=ci.quantity,
            price=ci.price,
            reason=ci.reason,
            rank=ci.rank,
        )
        if db is not None:
            await db["book_trades"].insert_one(trade_record)
        executed_trades.append(trade_record)

    # 3. MTM update
    snapshot = book.mark_to_market(prices, date_str=date_str)
    if db is not None:
        await db["book_equity"].update_one(
            {"date": date_str},
            {"$set": snapshot},
            upsert=True,
        )
        await book.save(db)

    # 4. Regime classification
    b_pct = breadth_pct if breadth_pct is not None else 50.0
    i_gap = index_gap_pct if index_gap_pct is not None else 0.0
    regime_res = classify_regime(b_pct, i_gap, vol_percentile)
    regime_doc = {
        "date": date_str,
        "regime": regime_res["regime"],
        "target_exposure": regime_res["target_exposure"],
        "breadth_pct": regime_res["breadth_pct"],
        "index_gap_pct": regime_res["index_gap_pct"],
        "vol_percentile": regime_res["vol_percentile"],
        "timestamp": datetime.now(timezone.utc),
    }
    if db is not None:
        await db["regime_log"].update_one(
            {"date": date_str},
            {"$set": regime_doc},
            upsert=True,
        )

    return {
        "status": "ok",
        "date": date_str,
        "snapshot": snapshot,
        "regime": regime_res,
        "catastrophic_trades": executed_trades,
    }


@require_v2_active(job_type="rebalance")
async def v2_rebalance_planner(
    db=None,
    date_str: Optional[str] = None,
    prices: Optional[Dict[str, float]] = None,
    ranked_universe: Optional[List[Any]] = None,
    target_exposure: Optional[float] = None,
) -> Dict[str, Any]:
    """15:40 IST Job: Generates and executes target portfolio for next day."""
    if db is None:
        from database import get_db
        db = get_db()

    date_str = date_str or ist_today_str()
    book = await Book.load(db)

    # Load last rebalance date and last regime
    last_rebalance = None
    last_regime = "NEUTRAL"
    current_regime = "NEUTRAL"

    if db is not None:
        state_doc = await db["system_state_v2"].find_one({"_id": "v2_state"}) or {}
        last_rebalance = state_doc.get("last_rebalance_date")
        last_regime = state_doc.get("last_regime", "NEUTRAL")

        regime_doc = await db["regime_log"].find_one({"date": date_str}) or {}
        current_regime = regime_doc.get("regime", "NEUTRAL")
        if target_exposure is None:
            target_exposure = regime_doc.get("target_exposure")

    # Cadence check
    if not should_rebalance(
        last_rebalance,
        date_str,
        cadence_days=60,
        regime=current_regime,
        prev_regime=last_regime,
    ):
        logger.info(f"V2 Rebalance skipped: cadence gate not met (last={last_rebalance}, today={date_str})")
        return {"status": "skipped", "reason": "cadence_gate", "intents": []}

    # Fallbacks for prices and ranked_universe
    if prices is None:
        prices = {}
        for ticker, h in book.holdings.items():
            prices[ticker] = h.get("current_price", h.get("avg_price", 0.0))

    if ranked_universe is None:
        ranked_universe = list(book.holdings.keys())

    # Generate intents
    intents = plan_rebalance(
        book=book,
        ranked_universe=ranked_universe,
        prices=prices,
        regime=current_regime,
        target_exposure=target_exposure,
        last_rebalance_date=last_rebalance,
        today_str=date_str,
        prev_regime=last_regime,
    )

    # Execute intents onto book and persist
    applied_trades = []
    for intent in intents:
        try:
            trade_rec = book.apply_trade(
                ticker=intent.ticker,
                side=intent.side,
                qty=intent.quantity,
                price=intent.price,
                reason=intent.reason,
                regime=current_regime,
                rank=intent.rank,
            )
            if db is not None:
                await db["book_trades"].insert_one(trade_rec)
            applied_trades.append(trade_rec)
        except Exception as e:
            logger.error(f"Error executing trade intent {intent}: {e}")

    if db is not None:
        await book.save(db)
        await db["system_state_v2"].update_one(
            {"_id": "v2_state"},
            {"$set": {
                "last_rebalance_date": date_str,
                "last_regime": current_regime,
                "updated_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )

    return {
        "status": "success",
        "date": date_str,
        "regime": current_regime,
        "intents": [it.to_dict() for it in intents],
        "applied_trades": applied_trades,
    }
