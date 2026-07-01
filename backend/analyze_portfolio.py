"""
One-shot portfolio analysis script — queries MongoDB for current portfolio state,
recent trades, and per-holding P&L breakdown.
"""
import asyncio
import json
import os
from pymongo import AsyncMongoClient
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv('MONGODB_URI', '')
DB_NAME = os.getenv('MONGODB_DB_NAME', 'paper_trader')

if not MONGO_URI:
    raise RuntimeError("MONGODB_URI not set in .env — refusing to start without credentials")


async def main():
    client = AsyncMongoClient(MONGO_URI)
    db = client[DB_NAME]

    # ── 1. Portfolio Snapshot ────────────────────────────────────────────
    portfolio = await db['portfolio'].find_one({"_id": "main"})
    if not portfolio:
        print("No portfolio document found!")
        return

    print("=" * 80)
    print("  PORTFOLIO SNAPSHOT")
    print("=" * 80)
    print(f"  Cash:            Rs. {portfolio.get('cash', 0):>14,.2f}")
    print(f"  Holdings Value:  Rs. {portfolio.get('holdings_value', 0):>14,.2f}")
    print(f"  Total Value:     Rs. {portfolio.get('total_value', 0):>14,.2f}")
    print(f"  Initial Balance: Rs. {portfolio.get('initial_balance', 0):>14,.2f}")
    print(f"  Peak Value:      Rs. {portfolio.get('peak_value', 0):>14,.2f}")
    print(f"  Total P&L:       Rs. {portfolio.get('total_pnl', 0):>14,.2f}")
    print(f"  Total P&L %:         {portfolio.get('total_pnl_pct', 0):>13.2f}%")
    print(f"  Updated At:      {portfolio.get('updated_at', 'N/A')}")
    print()

    # ── 2. Holdings Breakdown ───────────────────────────────────────────
    holdings = portfolio.get('holdings', [])
    print(f"  HOLDINGS ({len(holdings)} positions)")
    print("-" * 80)
    print(f"  {'Ticker':<18} {'Qty':>5} {'Avg Price':>10} {'Curr Price':>10} {'Mkt Value':>12} {'P&L':>10} {'P&L%':>8} {'Peak':>10}")
    print("-" * 80)

    total_invested = 0
    total_market_val = 0
    total_unrealized_pnl = 0
    holding_details = []

    for h in holdings:
        ticker = h.get('ticker', 'N/A')
        qty = h.get('quantity', 0)
        avg_price = h.get('avg_price', 0)
        current_price = h.get('current_price', 0)
        market_value = h.get('market_value', qty * current_price)
        pnl = h.get('unrealized_pnl', (current_price - avg_price) * qty)
        pnl_pct = h.get('unrealized_pnl_pct', ((current_price - avg_price) / avg_price * 100) if avg_price > 0 else 0)
        peak_price = h.get('peak_price', 0)
        sector = h.get('sector', '?')
        bought_at = h.get('bought_at', 'N/A')
        invested = avg_price * qty

        total_invested += invested
        total_market_val += market_value
        total_unrealized_pnl += pnl

        pnl_sign = "+" if pnl >= 0 else ""
        print(f"  {ticker:<18} {qty:>5} {avg_price:>10,.2f} {current_price:>10,.2f} {market_value:>12,.2f} {pnl_sign}{pnl:>9,.2f} {pnl_pct:>7.2f}% {peak_price:>10,.2f}")

        holding_details.append({
            'ticker': ticker,
            'qty': qty,
            'avg_price': avg_price,
            'current_price': current_price,
            'invested': invested,
            'market_value': market_value,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'peak_price': peak_price,
            'sector': sector,
            'bought_at': str(bought_at),
        })

    print("-" * 80)
    print(f"  {'TOTALS':<18} {'':>5} {'':>10} {'':>10} {total_market_val:>12,.2f} {'+' if total_unrealized_pnl >= 0 else ''}{total_unrealized_pnl:>9,.2f}")
    print(f"  Total Invested (cost basis): Rs. {total_invested:,.2f}")
    print()

    # ── 3. Worst-Case Crash Calculation ─────────────────────────────────
    print("=" * 80)
    print("  WORST-CASE CRASH ANALYSIS")
    print("=" * 80)

    cash = portfolio.get('cash', 0)
    initial_balance = portfolio.get('initial_balance', 0)

    # Scenario 1: All holdings drop to 0 (absolute worst case)
    max_loss_total_wipeout = total_market_val
    portfolio_after_wipeout = cash  # only cash survives
    loss_from_initial = initial_balance - cash

    print(f"\n  Scenario 1: 100% WIPEOUT (all stocks -> Rs. 0)")
    print(f"    Holdings wiped:     Rs. {max_loss_total_wipeout:>14,.2f}")
    print(f"    Surviving cash:     Rs. {cash:>14,.2f}")
    print(f"    Portfolio after:    Rs. {cash:>14,.2f}")
    print(f"    Loss from initial:  Rs. {loss_from_initial:>14,.2f} ({loss_from_initial/initial_balance*100:.2f}%)")

    # Scenario 2: Different crash levels
    crash_scenarios = [10, 20, 30, 40, 50, 60, 70, 80, 90]
    print(f"\n  Scenario 2: GRADUATED CRASH LEVELS")
    print(f"  {'Crash %':>8} {'Holdings After':>16} {'Portfolio After':>16} {'Loss from Now':>16} {'Loss from Initial':>18}")
    print(f"  {'-'*8} {'-'*16} {'-'*16} {'-'*16} {'-'*18}")

    current_total = portfolio.get('total_value', 0)
    for crash_pct in crash_scenarios:
        holdings_after = total_market_val * (1 - crash_pct / 100)
        portfolio_after = cash + holdings_after
        loss_from_now = current_total - portfolio_after
        loss_from_init = initial_balance - portfolio_after
        print(f"  {crash_pct:>7}% {holdings_after:>16,.2f} {portfolio_after:>16,.2f} {loss_from_now:>16,.2f} {loss_from_init:>16,.2f}")

    # Per-stock crash impact
    print(f"\n  Per-Stock Maximum Exposure (if each stock → Rs. 0)")
    print(f"  {'Ticker':<18} {'Invested':>12} {'Current Val':>12} {'Max Loss':>12} {'% of Portfolio':>14}")
    print(f"  {'-'*18} {'-'*12} {'-'*12} {'-'*12} {'-'*14}")
    for h in sorted(holding_details, key=lambda x: x['market_value'], reverse=True):
        pct_portfolio = (h['market_value'] / current_total * 100) if current_total > 0 else 0
        print(f"  {h['ticker']:<18} {h['invested']:>12,.2f} {h['market_value']:>12,.2f} {h['market_value']:>12,.2f} {pct_portfolio:>13.2f}%")

    print()

    # ── 4. Recent Trades (last 30 days) ─────────────────────────────────
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    trades_cursor = db['trades'].find(
        {"timestamp": {"$gte": thirty_days_ago}},
        {"_id": 0, "ticker": 1, "action": 1, "quantity": 1, "price": 1,
         "total_value": 1, "timestamp": 1, "charges": 1, "final_score": 1}
    ).sort("timestamp", -1)

    trades = await trades_cursor.to_list(length=100)

    print("=" * 80)
    print(f"  RECENT TRADES (last 30 days) — {len(trades)} trades")
    print("=" * 80)
    total_charges = 0
    total_buy_value = 0
    total_sell_value = 0
    for t in trades:
        action = t.get('action', '?')
        ticker = t.get('ticker', 'N/A')
        qty = t.get('quantity', 0)
        price = t.get('price', 0)
        total_val = t.get('total_value', 0)
        ts = t.get('timestamp', 'N/A')
        charges = t.get('charges', {})
        charge_total = charges.get('total_charges', 0) if charges else 0
        total_charges += charge_total
        if action == 'BUY':
            total_buy_value += total_val
        elif action == 'SELL':
            total_sell_value += total_val
        score = t.get('final_score', 'N/A')
        print(f"  {str(ts)[:19]}  {action:<5} {ticker:<18} {qty:>5}x @ Rs.{price:>10,.2f} = Rs.{total_val:>12,.2f} | Charges: Rs.{charge_total:>7,.2f} | Score: {score}")

    print("-" * 80)
    print(f"  Total BUY value:    Rs. {total_buy_value:>14,.2f}")
    print(f"  Total SELL value:   Rs. {total_sell_value:>14,.2f}")
    print(f"  Total Charges Paid: Rs. {total_charges:>14,.2f}")
    print()

    # ── 5. Portfolio History (recent snapshots) ─────────────────────────
    history_cursor = db['portfolio_history'].find(
        {}, {"_id": 0}
    ).sort("timestamp", -1).limit(10)
    history = await history_cursor.to_list(length=10)

    print("=" * 80)
    print(f"  PORTFOLIO VALUE HISTORY (last 10 snapshots)")
    print("=" * 80)
    for snap in history:
        ts = snap.get('timestamp', 'N/A')
        tv = snap.get('total_value', 0)
        c = snap.get('cash', 0)
        hv = snap.get('holdings_value', 0)
        print(f"  {str(ts)[:19]}  Total: Rs.{tv:>12,.2f}  Cash: Rs.{c:>12,.2f}  Holdings: Rs.{hv:>12,.2f}")

    print()
    print("=" * 80)
    print("  LOSS ANALYSIS SUMMARY")
    print("=" * 80)
    print(f"  Initial Balance:       Rs. {initial_balance:>14,.2f}")
    print(f"  Current Total Value:   Rs. {current_total:>14,.2f}")
    print(f"  Net P&L:               Rs. {current_total - initial_balance:>14,.2f}")
    print(f"  Total Charges Paid:    Rs. {total_charges:>14,.2f}")
    print(f"  Cash (uninvested):     Rs. {cash:>14,.2f}")
    print(f"  Holdings at Cost:      Rs. {total_invested:>14,.2f}")
    print(f"  Holdings at Market:    Rs. {total_market_val:>14,.2f}")
    print(f"  Unrealized P&L:        Rs. {total_unrealized_pnl:>14,.2f}")
    realized_pnl = (current_total - initial_balance) - total_unrealized_pnl
    print(f"  Realized P&L (est):    Rs. {realized_pnl:>14,.2f}")
    print()
    print(f"  ⚠  MAX POSSIBLE LOSS (100% crash):")
    print(f"     Current holdings value at risk: Rs. {total_market_val:>14,.2f}")
    print(f"     Portfolio would drop to:        Rs. {cash:>14,.2f} (cash only)")
    print(f"     Total loss from initial:        Rs. {loss_from_initial:>14,.2f}")
    print(f"     Loss percentage from initial:       {loss_from_initial/initial_balance*100:.2f}%")
    print()

    await client.close()


asyncio.run(main())
