"""Meta research portfolio endpoints — visibility + manual trigger."""
from fastapi import APIRouter
from database import (
    get_meta_portfolio_collection,
    get_meta_trades_collection,
    get_meta_equity_collection,
)
from meta_portfolio import rebalance_meta_portfolio

router = APIRouter(prefix="/meta", tags=["Meta Research"])


@router.get("/status")
async def meta_status():
    doc = await get_meta_portfolio_collection().find_one({"_id": "meta"}, {"_id": 0})
    trades = await get_meta_trades_collection().find(
        {}, {"_id": 0}).sort("timestamp", -1).limit(15).to_list(length=15)
    equity = await get_meta_equity_collection().find(
        {}, {"_id": 0}).sort("timestamp", -1).limit(30).to_list(length=30)
    return {"portfolio": doc, "recent_trades": trades, "equity": list(reversed(equity))}


@router.get("/equity")
async def meta_equity():
    docs = await get_meta_equity_collection().find(
        {}, {"_id": 0}).sort("timestamp", 1).to_list(length=500)
    return {"equity": docs, "count": len(docs)}


@router.post("/rebalance")
async def meta_rebalance():
    return await rebalance_meta_portfolio(force=True)
