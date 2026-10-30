import asyncio, os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))

async def main():
    c = AsyncIOMotorClient(os.environ["MONGODB_URI"])
    db = c["paper_trader"]
    p = await db.portfolio.find_one()
    if p:
        print(f"Cash: Rs {p.get('cash'):,.2f}")
        print(f"Total Value: Rs {p.get('total_value'):,.2f}")
        realized = p.get('realized_pnl') or 0.0
        print(f"Realized PnL: Rs {realized:,.2f}")
        print(f"Positions count: {len(p.get('positions', []))}")
        trades_count = await db.trades.count_documents({})
        print(f"Trades count: {trades_count}")
    else:
        print("No portfolio found")
    c.close()

if __name__ == "__main__":
    asyncio.run(main())
