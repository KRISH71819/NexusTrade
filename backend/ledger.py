"""
Trading Ledger — manages both paper and live trading portfolio operations.

Dual-mode architecture:
  - Paper mode: Virtual cash, simulated slippage/charges (existing behavior)
  - Live mode: Real orders via Dhan API, actual market execution

Both modes share the same analysis pipeline. Only the execution layer differs.
Kill switch blocks all BUYs in both modes but allows SELLs.
"""

from datetime import datetime, timezone
from typing import Optional
import logging
from pymongo.errors import PyMongoError

from database import (
    get_portfolio_collection,
    get_trades_collection,
    get_analysis_collection,
    get_portfolio_history_collection,
    get_portfolio_collection_for_mode,
    get_trades_collection_for_mode,
    get_portfolio_history_collection_for_mode,
    get_live_portfolio_collection,
)
from models import TradeAction, AnalysisResult, Portfolio, Holding
from config import settings

logger = logging.getLogger(__name__)

# Track last snapshot time to throttle frequency (max 1 per 15 min)
_last_snapshot_time: datetime | None = None
_SNAPSHOT_INTERVAL_SECONDS = 900  # 15 minutes


def calculate_trade_charges(turnover: float, side: str) -> dict:
    """
    Calculate Indian market trading charges for a delivery-based equity trade.

    Args:
        turnover: Total trade value (price × quantity)
        side: 'BUY' or 'SELL'

    Returns:
        dict with itemized charges and total
    """
    stt = turnover * (settings.stt_buy_pct if side == "BUY" else settings.stt_sell_pct)
    exchange_txn = turnover * settings.exchange_txn_charge_pct
    sebi_fee = turnover * settings.sebi_turnover_fee_pct
    stamp_duty = turnover * settings.stamp_duty_buy_pct if side == "BUY" else 0.0
    brokerage = settings.brokerage_per_order
    gst = (brokerage + exchange_txn) * settings.gst_pct

    total = stt + exchange_txn + sebi_fee + stamp_duty + brokerage + gst

    return {
        "stt": round(stt, 2),
        "exchange_txn": round(exchange_txn, 2),
        "sebi_fee": round(sebi_fee, 2),
        "stamp_duty": round(stamp_duty, 2),
        "brokerage": round(brokerage, 2),
        "gst": round(gst, 2),
        "total_charges": round(total, 2),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#   READ OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════

async def get_portfolio() -> dict:
    """Retrieve the current portfolio state."""
    try:
        collection = get_portfolio_collection()
        doc = await collection.find_one({"_id": "main"})
        if doc is None:
            raise RuntimeError("Portfolio not initialized")
        return doc
    except PyMongoError as e:
        logger.warning(f"MongoDB offline: {e}. Returning mock portfolio.")
        return {
            "cash": settings.initial_balance,
            "total_value": settings.initial_balance,
            "holdings": [],
            "initial_balance": settings.initial_balance,
            "peak_value": settings.initial_balance,
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
        }


async def get_portfolio_value() -> float:
    """Get total portfolio value (cash + holdings at market price)."""
    portfolio = await get_portfolio()
    return portfolio["total_value"]


async def reset_portfolio(initial_balance: float, clear_logs: bool = True) -> dict:
    """
    Reset the paper-trading account to a clean starting balance.
    This lets the dashboard/user restart the shadow portfolio at 0% profit.
    """
    if initial_balance <= 0:
        raise ValueError("initial_balance must be greater than 0")

    now = datetime.now(timezone.utc)
    portfolio_doc = {
        "_id": "main",
        "cash": round(initial_balance, 2),
        "holdings": [],
        "total_value": round(initial_balance, 2),
        "holdings_value": 0.0,
        "total_pnl": 0.0,
        "total_pnl_pct": 0.0,
        "initial_balance": round(initial_balance, 2),
        "peak_value": round(initial_balance, 2),
        "created_at": now,
        "updated_at": now,
    }

    collection = get_portfolio_collection()
    await collection.replace_one({"_id": "main"}, portfolio_doc, upsert=True)

    if clear_logs:
        await get_trades_collection().delete_many({})
        await get_analysis_collection().delete_many({})
        await get_portfolio_history_collection().delete_many({})

    await _record_snapshot(initial_balance, 0.0, initial_balance)
    logger.info(f"Portfolio reset with starting balance Rs {initial_balance:,.2f}")

    portfolio_doc.pop("_id", None)
    return portfolio_doc


async def get_holding(ticker: str) -> Optional[dict]:
    """Get holding for a specific ticker, or None if not held."""
    portfolio = await get_portfolio()
    for h in portfolio.get("holdings", []):
        if h["ticker"] == ticker:
            return h
    return None


async def has_position(ticker: str) -> bool:
    """Check if we currently hold a position in the ticker."""
    holding = await get_holding(ticker)
    return holding is not None and holding.get("quantity", 0) > 0


# ═══════════════════════════════════════════════════════════════════════════════
#   PORTFOLIO VALUATION (using live prices)
# ═══════════════════════════════════════════════════════════════════════════════

async def update_portfolio_valuation(live_prices: dict) -> dict:
    """
    Update portfolio total_value using live market prices.
    Also updates peak_price per holding for trailing stops.

    Args:
        live_prices: {ticker: current_price} mapping
    """
    portfolio = await get_portfolio()
    holdings = portfolio.get("holdings", [])
    cash = portfolio["cash"]

    holdings_value = 0.0
    for h in holdings:
        ticker = h["ticker"]
        current_price = live_prices.get(ticker) or h.get("avg_price", 0)
        qty = h.get("quantity", 0)
        avg_price = h.get("avg_price", 0)

        market_value = current_price * qty
        holdings_value += market_value

        # Update live valuation fields on the holding
        h["current_price"] = round(current_price, 2)
        h["market_value"] = round(market_value, 2)
        h["unrealized_pnl"] = round((current_price - avg_price) * qty, 2) if avg_price > 0 else 0.0
        h["unrealized_pnl_pct"] = round(
            ((current_price - avg_price) / avg_price) * 100, 2
        ) if avg_price > 0 else 0.0

        # Update peak_price for trailing stop (always track the highest price seen)
        peak = h.get("peak_price", avg_price or current_price)
        if current_price > peak:
            h["peak_price"] = round(current_price, 2)
        elif peak == 0:
            # Ensure peak_price is never 0
            h["peak_price"] = round(max(current_price, avg_price), 2)

    total_value = cash + holdings_value
    initial = portfolio.get("initial_balance", settings.initial_balance)

    # Update peak portfolio value
    peak_value = portfolio.get("peak_value", initial)
    if total_value > peak_value:
        peak_value = total_value

    try:
        collection = get_portfolio_collection()
        await collection.update_one(
            {"_id": "main"},
            {"$set": {
                "holdings": holdings,
                "total_value": round(total_value, 2),
                "holdings_value": round(holdings_value, 2),
                "peak_value": round(peak_value, 2),
                "total_pnl": round(total_value - initial, 2),
                "total_pnl_pct": round(
                    ((total_value - initial) / initial) * 100, 2
                ) if initial > 0 else 0.0,
                "updated_at": datetime.now(timezone.utc),
            }},
        )

        # Record periodic snapshot (throttled to max 1 per 15 minutes)
        global _last_snapshot_time
        now = datetime.now(timezone.utc)
        should_snapshot = (
            _last_snapshot_time is None
            or (now - _last_snapshot_time).total_seconds() >= _SNAPSHOT_INTERVAL_SECONDS
        )
        if should_snapshot:
            await _record_snapshot(cash, holdings_value, total_value)
            _last_snapshot_time = now
            logger.debug(f"Portfolio snapshot recorded: Rs {total_value:,.2f}")

    except PyMongoError as e:
        logger.warning(f"Could not update portfolio valuation: {e}")

    return {
        "cash": cash,
        "holdings_value": round(holdings_value, 2),
        "total_value": round(total_value, 2),
        "peak_value": round(peak_value, 2),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#   TRADE EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

async def execute_buy(
    ticker: str,
    price: float,
    analysis: AnalysisResult,
    quantity: Optional[int] = None,
    max_position_pct: Optional[float] = None,
    atr: Optional[float] = None,
) -> dict:
    """
    Execute a virtual BUY order with slippage simulation.
    If quantity is None, auto-size based on max_position_pct AND ATR risk.
    """
    # ── SLIPPAGE SIMULATION: add 0.15% premium to BUY price ─────────────
    # Real-world buys execute slightly above market price due to:
    # STT (~0.1%), brokerage (~0.03%), market impact (~0.02%)
    market_price = price
    slippage_mult = 1 + (settings.slippage_bps / 10_000)
    price = round(price * slippage_mult, 2)
    logger.info(
        f"[SLIPPAGE] {ticker} BUY: Market Rs.{market_price:.2f} → "
        f"Execution Rs.{price:.2f} (+{settings.slippage_bps:.0f}bps)"
    )

    portfolio = await get_portfolio()
    cash = portfolio["cash"]

    # ── Guardrail 1: Max open positions (diversification) ───────────────
    holdings_list = portfolio.get("holdings", [])
    holdings_count = len([h for h in holdings_list if h.get("quantity", 0) > 0])
    is_existing = any(h["ticker"] == ticker for h in holdings_list if h.get("quantity", 0) > 0)
    if not is_existing and holdings_count >= settings.max_open_positions:
        logger.warning(
            f"MAX POSITIONS: Already holding {holdings_count}/{settings.max_open_positions} stocks. "
            f"Cannot open new position in {ticker}."
        )
        return {"error": "Max positions reached", "ticker": ticker}

    # ── Guardrail 2: Tiny cash reserve (5% emergency buffer) ─────────
    min_reserve = portfolio["total_value"] * settings.min_cash_reserve_pct
    spendable_cash = max(0, cash - min_reserve)

    # ── Guardrail 3: Per-trade diversification cap ───────────────────
    position_pct = max_position_pct or settings.max_position_pct
    trade_cap = settings.max_single_trade_pct
    effective_pct = min(position_pct, trade_cap)
    max_spend = portfolio["total_value"] * effective_pct
    available = min(spendable_cash, max_spend)

    if available < price:
        logger.warning(
            f"Insufficient funds to buy {ticker} at Rs.{price:.2f} "
            f"(available: Rs.{available:.2f}, reserve: Rs.{min_reserve:.2f})"
        )
        return {"error": "Insufficient funds", "ticker": ticker}

    if quantity is None:
        # ── Confidence-based quantity (existing logic) ─────────────────
        confidence_qty = int(available // price)

        # ── ATR-based quantity cap (volatility-adjusted sizing) ─────────
        # Professional position sizing: risk 1% of portfolio per trade,
        # with stop at 1.5× ATR below entry. This means volatile stocks
        # automatically get smaller positions.
        if atr and atr > 0:
            risk_budget = portfolio["total_value"] * settings.atr_risk_per_trade_pct
            atr_stop_distance = atr * settings.atr_stop_multiplier
            atr_qty = int(risk_budget / atr_stop_distance) if atr_stop_distance > 0 else confidence_qty

            if atr_qty < confidence_qty:
                logger.info(
                    f"[ATR SIZING] {ticker}: ATR=Rs.{atr:.2f}, "
                    f"stop distance=Rs.{atr_stop_distance:.2f}, "
                    f"ATR qty={atr_qty} < confidence qty={confidence_qty}. "
                    f"Using ATR cap for volatility-adjusted sizing."
                )
            quantity = min(confidence_qty, atr_qty)
        else:
            quantity = confidence_qty

    if quantity <= 0:
        return {"error": "Computed quantity is 0", "ticker": ticker}

    total_cost = quantity * price

    # Calculate and deduct trading charges
    charges = calculate_trade_charges(total_cost, "BUY")
    total_cost_with_charges = total_cost + charges["total_charges"]
    new_cash = cash - total_cost_with_charges

    if new_cash < 0:
        # Not enough cash to cover cost + charges
        logger.warning(
            f"Insufficient funds after charges for {ticker}: "
            f"cost Rs.{total_cost:.2f} + charges Rs.{charges['total_charges']:.2f} "
            f"> cash Rs.{cash:.2f}"
        )
        return {"error": "Insufficient funds (after charges)", "ticker": ticker}

    logger.info(
        f"[CHARGES] {ticker} BUY: STT Rs.{charges['stt']:.2f}, "
        f"Brokerage Rs.{charges['brokerage']:.2f}, GST Rs.{charges['gst']:.2f}, "
        f"Total charges Rs.{charges['total_charges']:.2f}"
    )

    # Update holdings
    holdings = portfolio.get("holdings", [])
    existing = None
    for h in holdings:
        if h["ticker"] == ticker:
            existing = h
            break

    if existing:
        # Average up
        total_qty = existing["quantity"] + quantity
        avg_price = (
            (existing["avg_price"] * existing["quantity"]) + (price * quantity)
        ) / total_qty
        existing["quantity"] = total_qty
        existing["avg_price"] = round(avg_price, 2)
        # Update peak price
        existing["peak_price"] = max(existing.get("peak_price", 0), price)
        # Update bought_at to latest buy time (resets underperformer grace period)
        existing["bought_at"] = datetime.now(timezone.utc)
    else:
        from news_intelligence import get_sector
        holdings.append({
            "ticker": ticker,
            "quantity": quantity,
            "avg_price": round(price, 2),
            "peak_price": round(price, 2),
            "sector": get_sector(ticker),
            "bought_at": datetime.now(timezone.utc),
            "profit_taken_tiers": [],       # tracks which profit tiers have fired
            "locked_stop_price": None,       # break-even lock after tier 1
        })

    # Compute new total value using live price for all holdings
    holdings_value = sum(h["quantity"] * price if h["ticker"] == ticker
                        else h["quantity"] * h.get("avg_price", 0)
                        for h in holdings)
    total_value = new_cash + holdings_value
    initial = portfolio.get("initial_balance", settings.initial_balance)

    # Update peak
    peak_value = max(portfolio.get("peak_value", initial), total_value)

    # Update portfolio in DB
    try:
        collection = get_portfolio_collection()
        await collection.update_one(
            {"_id": "main"},
            {"$set": {
                "cash": round(new_cash, 2),
                "holdings": holdings,
                "total_value": round(total_value, 2),
                "holdings_value": round(holdings_value, 2),
                "peak_value": round(peak_value, 2),
                "total_pnl": round(total_value - initial, 2),
                "total_pnl_pct": round(
                    ((total_value - initial) / initial) * 100, 2
                ),
                "updated_at": datetime.now(timezone.utc),
            }},
        )

        # Log the trade with full transparency
        trade_doc = _build_trade_doc(
            ticker=ticker,
            action=TradeAction.BUY,
            quantity=quantity,
            price=price,
            total_value=total_cost,
            analysis=analysis,
            cash_after=new_cash,
            portfolio_total=total_value,
            charges=charges,
        )
        await get_trades_collection().insert_one(trade_doc)
        await _log_analysis_decision(ticker, TradeAction.BUY, analysis)

        # Record portfolio snapshot
        await _record_snapshot(new_cash, holdings_value, total_value)
    except PyMongoError as e:
        logger.warning(f"Could not log BUY trade to MongoDB: {e}")
        trade_doc = _build_trade_doc(
            ticker=ticker, action=TradeAction.BUY, quantity=quantity, price=price,
            total_value=total_cost, analysis=analysis, cash_after=new_cash, portfolio_total=total_value,
            charges=charges,
        )

    logger.info(
        f"BUY {quantity}x {ticker} @ Rs.{price:.2f} = Rs.{total_cost:.2f} | "
        f"Cash remaining: Rs.{new_cash:.2f}"
    )

    return trade_doc


async def execute_sell(
    ticker: str,
    price: float,
    analysis: AnalysisResult,
    quantity: Optional[int] = None,
) -> dict:
    """
    Execute a virtual SELL order with slippage simulation.
    If quantity is None, sell the entire position.
    """
    # ── SLIPPAGE SIMULATION: subtract 0.15% from SELL price ───────────
    market_price = price
    slippage_mult = 1 - (settings.slippage_bps / 10_000)
    price = round(price * slippage_mult, 2)
    logger.info(
        f"[SLIPPAGE] {ticker} SELL: Market Rs.{market_price:.2f} → "
        f"Execution Rs.{price:.2f} (-{settings.slippage_bps:.0f}bps)"
    )

    portfolio = await get_portfolio()
    holdings = portfolio.get("holdings", [])

    existing = None
    for h in holdings:
        if h["ticker"] == ticker:
            existing = h
            break

    if not existing or existing["quantity"] <= 0:
        logger.warning(f"No position to sell for {ticker}")
        return {"error": "No position", "ticker": ticker}

    if quantity is None:
        quantity = existing["quantity"]

    quantity = min(quantity, existing["quantity"])
    total_proceeds = quantity * price

    # Calculate and deduct trading charges from sell proceeds
    charges = calculate_trade_charges(total_proceeds, "SELL")
    net_proceeds = total_proceeds - charges["total_charges"]

    logger.info(
        f"[CHARGES] {ticker} SELL: STT Rs.{charges['stt']:.2f}, "
        f"Brokerage Rs.{charges['brokerage']:.2f}, GST Rs.{charges['gst']:.2f}, "
        f"Total charges Rs.{charges['total_charges']:.2f}"
    )

    # Update holding
    existing["quantity"] -= quantity
    if existing["quantity"] <= 0:
        holdings = [h for h in holdings if h["ticker"] != ticker]

    new_cash = portfolio["cash"] + net_proceeds
    # Use current_price (live) for remaining holdings valuation, not avg_price
    holdings_value = sum(
        h["quantity"] * h.get("current_price", h.get("avg_price", 0))
        for h in holdings
    )
    total_value = new_cash + holdings_value
    initial = portfolio.get("initial_balance", settings.initial_balance)

    # Update peak
    peak_value = max(portfolio.get("peak_value", initial), total_value)

    # Update portfolio in DB
    try:
        collection = get_portfolio_collection()
        await collection.update_one(
            {"_id": "main"},
            {"$set": {
                "cash": round(new_cash, 2),
                "holdings": holdings,
                "total_value": round(total_value, 2),
                "holdings_value": round(holdings_value, 2),
                "peak_value": round(peak_value, 2),
                "total_pnl": round(total_value - initial, 2),
                "total_pnl_pct": round(
                    ((total_value - initial) / initial) * 100, 2
                ),
                "updated_at": datetime.now(timezone.utc),
            }},
        )

        # Log trade
        trade_doc = _build_trade_doc(
            ticker=ticker,
            action=TradeAction.SELL,
            quantity=quantity,
            price=price,
            total_value=total_proceeds,
            analysis=analysis,
            cash_after=new_cash,
            portfolio_total=total_value,
            charges=charges,
        )
        await get_trades_collection().insert_one(trade_doc)
        await _log_analysis_decision(ticker, TradeAction.SELL, analysis)

        # Record snapshot
        await _record_snapshot(new_cash, holdings_value, total_value)
    except PyMongoError as e:
        logger.warning(f"Could not log SELL trade to MongoDB: {e}")
        trade_doc = _build_trade_doc(
            ticker=ticker, action=TradeAction.SELL, quantity=quantity, price=price,
            total_value=total_proceeds, analysis=analysis, cash_after=new_cash, portfolio_total=total_value,
            charges=charges,
        )

    logger.info(
        f"SELL {quantity}x {ticker} @ Rs.{price:.2f} = Rs.{total_proceeds:.2f} | "
        f"Cash now: Rs.{new_cash:.2f}"
    )

    return trade_doc


async def log_hold(ticker: str, analysis: AnalysisResult) -> dict:
    """Log a HOLD decision (no trade) with full analysis for transparency."""
    analysis_doc = {
        "ticker": ticker,
        "timestamp": datetime.now(timezone.utc),
        "action": TradeAction.HOLD.value,
        "current_price": analysis.current_price,
        "ml_confidence": analysis.ml_confidence,
        "ml_features_used": analysis.ml_features_used,
        "news_headlines": analysis.news_headlines,
        "gemini_sentiment_score": analysis.gemini_sentiment_score,
        "gemini_explanation": analysis.gemini_explanation,
        "gemini_confidence": analysis.gemini_confidence,
        "final_score": analysis.final_score,
        "crisis_detected": analysis.crisis_detected,
        "action_reason": analysis.action_reason,
    }
    try:
        await get_analysis_collection().insert_one(analysis_doc)
    except PyMongoError as e:
        logger.warning(f"Could not log HOLD decision to MongoDB: {e}")

    logger.info(
        f"HOLD {ticker} — Score: {analysis.final_score:.2f}, "
        f"ML: {analysis.ml_confidence:.2f}, "
        f"Gemini: {analysis.gemini_confidence:.2f}"
    )

    return analysis_doc


# ═══════════════════════════════════════════════════════════════════════════════
#   HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

async def _log_analysis_decision(
    ticker: str,
    action: TradeAction,
    analysis: AnalysisResult,
) -> dict:
    """Log every AI decision, including BUY/SELL/HOLD, to analysis_log."""
    analysis_doc = {
        "ticker": ticker,
        "timestamp": datetime.now(timezone.utc),
        "action": action.value,
        "current_price": analysis.current_price,
        "ml_confidence": analysis.ml_confidence,
        "ml_features_used": analysis.ml_features_used,
        "news_headlines": analysis.news_headlines,
        "gemini_sentiment_score": analysis.gemini_sentiment_score,
        "gemini_explanation": analysis.gemini_explanation,
        "gemini_confidence": analysis.gemini_confidence,
        "gemini_risk_factors": analysis.gemini_risk_factors,
        "final_score": analysis.final_score,
        "crisis_detected": analysis.crisis_detected,
        "action_reason": analysis.action_reason,
    }
    try:
        await get_analysis_collection().insert_one(analysis_doc)
    except PyMongoError as e:
        logger.warning(f"Could not log {action.value} decision to MongoDB: {e}")

    return analysis_doc


def _build_trade_doc(
    ticker: str,
    action: TradeAction,
    quantity: int,
    price: float,
    total_value: float,
    analysis: AnalysisResult,
    cash_after: float,
    portfolio_total: float,
    charges: dict | None = None,
) -> dict:
    """Build a fully transparent trade document."""
    doc = {
        "timestamp": datetime.now(timezone.utc),
        "ticker": ticker,
        "action": action.value,
        "quantity": quantity,
        "price": round(price, 2),
        "total_value": round(total_value, 2),
        # Full AI brain transparency
        "ml_confidence": analysis.ml_confidence,
        "news_headlines": analysis.news_headlines,
        "gemini_sentiment_score": analysis.gemini_sentiment_score,
        "gemini_explanation": analysis.gemini_explanation,
        "gemini_confidence": analysis.gemini_confidence,
        "final_score": analysis.final_score,
        "crisis_detected": analysis.crisis_detected,
        "action_reason": analysis.action_reason,
        # Portfolio state after trade
        "portfolio_snapshot": {
            "timestamp": datetime.now(timezone.utc),
            "cash": round(cash_after, 2),
            "holdings_value": round(portfolio_total - cash_after, 2),
            "total_value": round(portfolio_total, 2),
        },
    }
    # Include itemized trading charges if provided
    if charges:
        doc["charges"] = charges
    return doc


async def _record_snapshot(cash: float, holdings_value: float, total_value: float, mode: str = "paper"):
    """Record a portfolio value snapshot for historical tracking."""
    await get_portfolio_history_collection_for_mode(mode).insert_one({
        "timestamp": datetime.now(timezone.utc),
        "cash": round(cash, 2),
        "holdings_value": round(holdings_value, 2),
        "total_value": round(total_value, 2),
        "mode": mode,
    })


# ═══════════════════════════════════════════════════════════════════════════════
#   LIVE TRADING (Dhan API)
# ═══════════════════════════════════════════════════════════════════════════════

async def get_live_portfolio() -> dict:
    """Get live portfolio state synced from Dhan account."""
    try:
        collection = get_live_portfolio_collection()
        doc = await collection.find_one({"_id": "main"})

        if doc is None:
            # First time — sync from Dhan
            return await sync_live_portfolio()

        return doc
    except PyMongoError as e:
        logger.warning(f"MongoDB offline: {e}. Returning empty live portfolio.")
        return {
            "cash": 0,
            "total_value": 0,
            "holdings": [],
            "initial_balance": 0,
            "peak_value": 0,
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
            "mode": "live",
        }


async def sync_live_portfolio() -> dict:
    """Sync live portfolio with Dhan account (funds + holdings)."""
    from dhan_client import dhan_client

    try:
        # Fetch real account data from Dhan
        funds_result = await dhan_client.get_fund_limits()
        holdings_result = await dhan_client.get_holdings()

        # Parse funds
        funds_data = funds_result.get("data", funds_result) if isinstance(funds_result, dict) else {}
        available_balance = float(funds_data.get("availabelBalance", funds_data.get("availableBalance", 0)))

        # Parse holdings
        holdings_list = []
        holdings_value = 0.0
        raw_holdings = []

        if isinstance(holdings_result, dict):
            raw_holdings = holdings_result.get("data", holdings_result.get("holdings", []))
            if isinstance(raw_holdings, dict):
                raw_holdings = [raw_holdings]

        if isinstance(raw_holdings, list):
            for h in raw_holdings:
                if not isinstance(h, dict):
                    continue
                qty = int(h.get("totalQty", h.get("quantity", 0)))
                if qty <= 0:
                    continue

                avg_price = float(h.get("avgCostPrice", h.get("avgPrice", 0)))
                current_price = float(h.get("lastTradedPrice", h.get("ltp", avg_price)))
                market_value = current_price * qty
                holdings_value += market_value

                holdings_list.append({
                    "ticker": h.get("tradingSymbol", h.get("symbol", "UNKNOWN")),
                    "quantity": qty,
                    "avg_price": round(avg_price, 2),
                    "current_price": round(current_price, 2),
                    "market_value": round(market_value, 2),
                    "unrealized_pnl": round((current_price - avg_price) * qty, 2),
                    "unrealized_pnl_pct": round(
                        ((current_price - avg_price) / avg_price) * 100, 2
                    ) if avg_price > 0 else 0.0,
                    "peak_price": round(max(current_price, avg_price), 2),
                    "security_id": str(h.get("securityId", h.get("security_id", ""))),
                    "exchange": h.get("exchange", "NSE"),
                })

        total_value = available_balance + holdings_value

        # Upsert to live_portfolio collection
        portfolio_doc = {
            "_id": "main",
            "cash": round(available_balance, 2),
            "holdings": holdings_list,
            "total_value": round(total_value, 2),
            "holdings_value": round(holdings_value, 2),
            "initial_balance": round(total_value, 2),  # Set initial as current on first sync
            "peak_value": round(total_value, 2),
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
            "mode": "live",
            "synced_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }

        collection = get_live_portfolio_collection()

        # Preserve initial_balance and peak_value from existing doc
        existing = await collection.find_one({"_id": "main"})
        if existing:
            portfolio_doc["initial_balance"] = existing.get("initial_balance", total_value)
            portfolio_doc["peak_value"] = max(existing.get("peak_value", 0), total_value)
            initial = portfolio_doc["initial_balance"]
            portfolio_doc["total_pnl"] = round(total_value - initial, 2)
            portfolio_doc["total_pnl_pct"] = round(
                ((total_value - initial) / initial) * 100, 2
            ) if initial > 0 else 0.0

        await collection.replace_one({"_id": "main"}, portfolio_doc, upsert=True)

        logger.info(
            f"Live portfolio synced: Cash Rs.{available_balance:,.2f}, "
            f"{len(holdings_list)} holdings, Total Rs.{total_value:,.2f}"
        )

        portfolio_doc.pop("_id", None)
        return portfolio_doc

    except Exception as e:
        logger.error(f"Failed to sync live portfolio: {e}")
        return {"error": str(e), "mode": "live"}


async def execute_live_buy(
    ticker: str,
    price: float,
    analysis: AnalysisResult,
    quantity: Optional[int] = None,
    max_position_pct: Optional[float] = None,
    atr: Optional[float] = None,
) -> dict:
    """
    Execute a REAL BUY order via Dhan API.
    Places the order on Dhan FIRST, then records to live_trades if successful.
    """
    from dhan_client import dhan_client
    from security_master import security_master
    from kill_switch import is_kill_switch_on

    # Kill switch check
    if await is_kill_switch_on():
        logger.warning(f"KILL SWITCH ACTIVE — blocking LIVE BUY for {ticker}")
        return {"error": "Kill switch active — buys blocked", "ticker": ticker}

    # Get Dhan security ID
    security_id = security_master.get_security_id(ticker)
    if not security_id:
        logger.error(f"No Dhan security ID found for {ticker} — cannot place live order")
        return {"error": f"Security ID not found for {ticker}", "ticker": ticker}

    # Get live portfolio for sizing
    portfolio = await get_live_portfolio()
    if portfolio.get("error"):
        return {"error": f"Cannot get live portfolio: {portfolio['error']}", "ticker": ticker}

    cash = portfolio.get("cash", 0)

    # Auto-size quantity if not provided
    if quantity is None:
        position_pct = max_position_pct or settings.max_position_pct
        trade_cap = settings.max_single_trade_pct
        effective_pct = min(position_pct, trade_cap)
        max_spend = portfolio.get("total_value", cash) * effective_pct

        # Cash reserve
        min_reserve = portfolio.get("total_value", cash) * settings.min_cash_reserve_pct
        spendable = max(0, cash - min_reserve)
        available = min(spendable, max_spend)
        
        # Apply Live Capital Cap (if > 0, otherwise it's Full Investment mode)
        live_cap = getattr(settings, "live_capital_cap", 100000.0)
        if live_cap > 0:
            current_invested = sum(h.get("quantity", 0) * h.get("avg_price", 0) for h in portfolio.get("holdings", []))
            allowed_by_cap = max(0.0, live_cap - current_invested)
            available = min(available, allowed_by_cap)

        if available < price:
            return {"error": "Insufficient funds for live buy", "ticker": ticker}

        quantity = int(available // price)

        # ATR-based cap
        if atr and atr > 0:
            risk_budget = portfolio.get("total_value", cash) * settings.atr_risk_per_trade_pct
            atr_stop_distance = atr * settings.atr_stop_multiplier
            if atr_stop_distance > 0:
                atr_qty = int(risk_budget / atr_stop_distance)
                quantity = min(quantity, atr_qty)

    if quantity <= 0:
        return {"error": "Computed quantity is 0", "ticker": ticker}

    # Pre-check: verify sufficient balance via Dhan
    funds = await dhan_client.get_fund_limits()
    if isinstance(funds, dict):
        funds_data = funds.get("data", funds)
        dhan_balance = float(funds_data.get("availabelBalance", funds_data.get("availableBalance", 0)))
        total_cost_estimate = quantity * price
        if dhan_balance < total_cost_estimate:
            logger.warning(
                f"Dhan balance Rs.{dhan_balance:,.2f} < estimated cost Rs.{total_cost_estimate:,.2f}"
            )
            return {"error": f"Insufficient Dhan balance (Rs.{dhan_balance:,.2f})", "ticker": ticker}

    # ── PLACE REAL ORDER ON DHAN ──────────────────────────────────────
    logger.info(f"🔴 PLACING LIVE BUY ORDER: {quantity}x {ticker} (security_id={security_id})")
    result = await dhan_client.place_buy_order(
        security_id=security_id,
        quantity=quantity,
        order_type="MARKET",
    )

    if result.get("status") != "success":
        error = result.get("error", "Unknown Dhan error")
        logger.error(f"LIVE BUY FAILED for {ticker}: {error}")
        return {"error": f"Dhan order failed: {error}", "ticker": ticker, "dhan_response": result}

    order_id = result.get("order_id", "unknown")
    logger.info(f"✅ LIVE BUY SUCCESS: {quantity}x {ticker}, Order ID: {order_id}")

    # Record trade in live_trades collection
    trade_doc = _build_trade_doc(
        ticker=ticker,
        action=TradeAction.BUY,
        quantity=quantity,
        price=price,
        total_value=quantity * price,
        analysis=analysis,
        cash_after=cash - (quantity * price),
        portfolio_total=portfolio.get("total_value", 0),
        charges=None,  # Dhan handles charges directly
    )
    trade_doc["mode"] = "live"
    trade_doc["dhan_order_id"] = order_id
    trade_doc["dhan_security_id"] = security_id

    try:
        await get_trades_collection_for_mode("live").insert_one(trade_doc)
        await _log_analysis_decision(ticker, TradeAction.BUY, analysis)
    except PyMongoError as e:
        logger.warning(f"Could not log live BUY trade to MongoDB: {e}")

    # Sync portfolio after trade
    await sync_live_portfolio()

    return trade_doc


async def execute_live_sell(
    ticker: str,
    price: float,
    analysis: AnalysisResult,
    quantity: Optional[int] = None,
) -> dict:
    """
    Execute a REAL SELL order via Dhan API.
    Places the order on Dhan FIRST, then records to live_trades if successful.
    """
    from dhan_client import dhan_client
    from security_master import security_master

    # Get Dhan security ID
    security_id = security_master.get_security_id(ticker)
    if not security_id:
        logger.error(f"No Dhan security ID found for {ticker} — cannot place live sell")
        return {"error": f"Security ID not found for {ticker}", "ticker": ticker}

    # Get live portfolio to check holdings
    portfolio = await get_live_portfolio()
    holdings = portfolio.get("holdings", [])

    existing = None
    for h in holdings:
        if h.get("ticker", "").upper() == ticker.upper():
            existing = h
            break

    if not existing or existing.get("quantity", 0) <= 0:
        logger.warning(f"No live position to sell for {ticker}")
        return {"error": "No position", "ticker": ticker}

    if quantity is None:
        quantity = existing["quantity"]

    quantity = min(quantity, existing["quantity"])

    # ── PLACE REAL ORDER ON DHAN ──────────────────────────────────────
    logger.info(f"🔴 PLACING LIVE SELL ORDER: {quantity}x {ticker} (security_id={security_id})")
    result = await dhan_client.place_sell_order(
        security_id=security_id,
        quantity=quantity,
        order_type="MARKET",
    )

    if result.get("status") != "success":
        error = result.get("error", "Unknown Dhan error")
        logger.error(f"LIVE SELL FAILED for {ticker}: {error}")
        return {"error": f"Dhan order failed: {error}", "ticker": ticker, "dhan_response": result}

    order_id = result.get("order_id", "unknown")
    logger.info(f"✅ LIVE SELL SUCCESS: {quantity}x {ticker}, Order ID: {order_id}")

    # Record trade
    trade_doc = _build_trade_doc(
        ticker=ticker,
        action=TradeAction.SELL,
        quantity=quantity,
        price=price,
        total_value=quantity * price,
        analysis=analysis,
        cash_after=portfolio.get("cash", 0) + (quantity * price),
        portfolio_total=portfolio.get("total_value", 0),
        charges=None,
    )
    trade_doc["mode"] = "live"
    trade_doc["dhan_order_id"] = order_id
    trade_doc["dhan_security_id"] = security_id

    try:
        await get_trades_collection_for_mode("live").insert_one(trade_doc)
        await _log_analysis_decision(ticker, TradeAction.SELL, analysis)
    except PyMongoError as e:
        logger.warning(f"Could not log live SELL trade to MongoDB: {e}")

    # Sync portfolio after trade
    await sync_live_portfolio()

    return trade_doc


# ═══════════════════════════════════════════════════════════════════════════════
#   MODE-AWARE WRAPPERS (used by scheduler)
# ═══════════════════════════════════════════════════════════════════════════════

async def execute_buy_for_mode(
    ticker: str,
    price: float,
    analysis: AnalysisResult,
    mode: str = "paper",
    quantity: Optional[int] = None,
    max_position_pct: Optional[float] = None,
    atr: Optional[float] = None,
) -> dict:
    """Route buy execution to the correct mode handler."""
    from kill_switch import is_kill_switch_on

    # Kill switch check (only applies to live mode)
    if mode == "live" and await is_kill_switch_on():
        logger.warning(f"KILL SWITCH ACTIVE — blocking BUY for {ticker} (mode={mode})")
        return {"error": "Kill switch active — buys blocked", "ticker": ticker}

    if mode == "live":
        return await execute_live_buy(ticker, price, analysis, quantity, max_position_pct, atr)
    else:
        return await execute_buy(ticker, price, analysis, quantity, max_position_pct, atr)


async def execute_sell_for_mode(
    ticker: str,
    price: float,
    analysis: AnalysisResult,
    mode: str = "paper",
    quantity: Optional[int] = None,
) -> dict:
    """Route sell execution to the correct mode handler."""
    if mode == "live":
        return await execute_live_sell(ticker, price, analysis, quantity)
    else:
        return await execute_sell(ticker, price, analysis, quantity)


async def get_portfolio_for_mode(mode: str = "paper") -> dict:
    """Get portfolio for the specified trading mode."""
    if mode == "live":
        portfolio = await get_live_portfolio()
    else:
        portfolio = await get_portfolio()

    portfolio["mode"] = mode
    return portfolio


async def has_position_for_mode(ticker: str, mode: str = "paper") -> bool:
    """Check if we hold a position in the given mode."""
    portfolio = await get_portfolio_for_mode(mode)
    for h in portfolio.get("holdings", []):
        if h.get("ticker", "").upper() == ticker.upper() and h.get("quantity", 0) > 0:
            return True
    return False


async def get_current_trading_mode() -> str:
    """Get the currently active trading mode from config."""
    return settings.trading_mode
