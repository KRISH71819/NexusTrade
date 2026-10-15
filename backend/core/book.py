"""
Book Engine & State Machine (v2 Phase 4).
Single source of truth for portfolio accounting on strictly EOD daily bars.
Tracks Cash, Holdings, Weighted-Average Cost, P&L, and persists immutable trades.
"""
from datetime import datetime, timezone
import logging
from typing import Dict, Any, List, Optional

from core.costs import calculate_trade_charges
from core.ist import ist_today_str

logger = logging.getLogger(__name__)

class Book:
    def __init__(self, cash: float = 100_000.0, holdings: Optional[Dict[str, Dict[str, Any]]] = None,
                 initial_balance: float = 100_000.0, realized_pnl: float = 0.0):
        self.cash = float(cash)
        self.holdings = holdings if holdings is not None else {}
        self.initial_balance = float(initial_balance)
        self.realized_pnl = float(realized_pnl)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Book":
        raw_holdings = data.get("holdings", [])
        if isinstance(raw_holdings, list):
            holdings_dict = {h["ticker"]: dict(h) for h in raw_holdings if "ticker" in h}
        elif isinstance(raw_holdings, dict):
            holdings_dict = dict(raw_holdings)
        else:
            holdings_dict = {}

        return cls(
            cash=float(data.get("cash", 100_000.0)),
            holdings=holdings_dict,
            initial_balance=float(data.get("initial_balance", 100_000.0)),
            realized_pnl=float(data.get("realized_pnl", 0.0)),
        )

    def to_dict(self) -> Dict[str, Any]:
        holdings_list = list(self.holdings.values())
        holdings_val = sum(h.get("quantity", 0) * h.get("current_price", h.get("avg_price", 0.0))
                           for h in holdings_list)
        total_val = self.cash + holdings_val
        return {
            "_id": "main",
            "cash": round(self.cash, 2),
            "holdings": holdings_list,
            "holdings_value": round(holdings_val, 2),
            "total_value": round(total_val, 2),
            "total_equity": round(total_val, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "initial_balance": self.initial_balance,
            "updated_at": datetime.now(timezone.utc),
        }

    async def save(self, db=None) -> None:
        if db is None:
            from database import get_db
            db = get_db()
        await db["book"].update_one({"_id": "main"}, {"$set": self.to_dict()}, upsert=True)

    @classmethod
    async def load(cls, db=None) -> "Book":
        if db is None:
            from database import get_db
            db = get_db()
        doc = await db["book"].find_one({"_id": "main"})
        if not doc:
            book = cls()
            await book.save(db)
            return book
        return cls.from_dict(doc)

    def apply_trade(self, ticker: str, side: str, qty: int, price: float,
                    reason: str = "", regime: str = "", rank: Optional[int] = None) -> Dict[str, Any]:
        if side not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side: {side}")
        if qty <= 0:
            raise ValueError(f"Quantity must be > 0 (got {qty})")
        if price <= 0:
            raise ValueError(f"Price must be > 0 (got {price})")

        turnover = round(qty * price, 2)
        charges = calculate_trade_charges(turnover, side)
        total_charges = charges["total_charges"]

        if side == "BUY":
            total_cost = turnover + total_charges
            if self.cash < total_cost:
                raise ValueError(f"Insufficient cash for BUY: needed {total_cost:.2f}, available {self.cash:.2f}")

            old_holding = self.holdings.get(ticker, {"quantity": 0, "avg_price": 0.0})
            old_qty = old_holding.get("quantity", 0)
            old_avg = old_holding.get("avg_price", 0.0)

            new_qty = old_qty + qty
            new_avg = ((old_qty * old_avg) + turnover) / new_qty

            self.cash -= total_cost
            self.holdings[ticker] = {
                "ticker": ticker,
                "quantity": new_qty,
                "avg_price": round(new_avg, 2),
                "current_price": price,
                "pnl": round((price - new_avg) * new_qty, 2),
                "pnl_pct": round(((price / new_avg) - 1.0) * 100, 2) if new_avg > 0 else 0.0,
            }
            realized_pnl = 0.0

        else:  # SELL
            old_holding = self.holdings.get(ticker)
            if not old_holding or old_holding.get("quantity", 0) < qty:
                available = old_holding.get("quantity", 0) if old_holding else 0
                raise ValueError(f"Insufficient quantity to SELL {ticker}: needed {qty}, held {available}")

            old_avg = old_holding.get("avg_price", 0.0)
            old_qty = old_holding.get("quantity", 0)
            proceeds = turnover - total_charges
            realized_pnl = round(((price - old_avg) * qty) - total_charges, 2)

            self.cash += proceeds
            self.realized_pnl += realized_pnl

            new_qty = old_qty - qty
            if new_qty == 0:
                self.holdings.pop(ticker, None)
            else:
                self.holdings[ticker]["quantity"] = new_qty
                self.holdings[ticker]["current_price"] = price
                self.holdings[ticker]["pnl"] = round((price - old_avg) * new_qty, 2)
                self.holdings[ticker]["pnl_pct"] = round(((price / old_avg) - 1.0) * 100, 2) if old_avg > 0 else 0.0

        trade_record = {
            "ticker": ticker,
            "side": side,
            "quantity": qty,
            "price": price,
            "turnover": turnover,
            "charges": charges,
            "realized_pnl": realized_pnl,
            "reason": reason,
            "regime": regime,
            "rank": rank,
            "timestamp": datetime.now(timezone.utc),
        }
        return trade_record

    def mark_to_market(self, prices: Dict[str, float], date_str: Optional[str] = None) -> Dict[str, Any]:
        holdings_val = 0.0
        unrealized_pnl = 0.0

        for ticker, h in self.holdings.items():
            qty = h.get("quantity", 0)
            if qty <= 0:
                continue
            cur_p = prices.get(ticker, h.get("current_price", h.get("avg_price", 0.0)))
            h["current_price"] = cur_p
            avg_p = h.get("avg_price", cur_p)
            pnl = (cur_p - avg_p) * qty
            h["pnl"] = round(pnl, 2)
            h["pnl_pct"] = round(((cur_p / avg_p) - 1.0) * 100, 2) if avg_p > 0 else 0.0
            holdings_val += qty * cur_p
            unrealized_pnl += pnl

        total_equity = self.cash + holdings_val
        total_pnl = total_equity - self.initial_balance
        total_pnl_pct = (total_pnl / self.initial_balance) * 100 if self.initial_balance > 0 else 0.0

        snapshot = {
            "date": date_str or ist_today_str(),
            "timestamp": datetime.now(timezone.utc),
            "cash": round(self.cash, 2),
            "holdings_value": round(holdings_val, 2),
            "total_equity": round(total_equity, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "num_holdings": len([h for h in self.holdings.values() if h.get("quantity", 0) > 0]),
        }
        return snapshot

    def get_exposure(self) -> float:
        holdings_val = sum(h.get("quantity", 0) * h.get("current_price", h.get("avg_price", 0.0))
                           for h in self.holdings.values())
        total_equity = self.cash + holdings_val
        return holdings_val / total_equity if total_equity > 0 else 0.0
