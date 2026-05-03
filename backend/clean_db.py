import asyncio
from pymongo.asynchronous.mongo_client import AsyncMongoClient

async def clean():
    client = AsyncMongoClient('REDACTED_USE_ENV_VAR')
    db = client['paper_trader']
    await db.drop_collection('market_data')
    await db.drop_collection('analysis')
    await db.drop_collection('trades')
    await db.drop_collection('portfolio')
    print('Dropped collections')

if __name__ == "__main__":
    asyncio.run(clean())
