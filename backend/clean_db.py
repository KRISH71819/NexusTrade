import asyncio
import os
from pymongo.asynchronous.mongo_client import AsyncMongoClient
from dotenv import load_dotenv

load_dotenv()

async def clean():
    uri = os.getenv('MONGODB_URI', '')
    if not uri:
        raise RuntimeError("MONGODB_URI not set in .env")
    client = AsyncMongoClient(uri)
    db = client[os.getenv('MONGODB_DB_NAME', 'paper_trader')]
    await db.drop_collection('market_data')
    await db.drop_collection('analysis')
    await db.drop_collection('trades')
    await db.drop_collection('portfolio')
    print('Dropped collections')

if __name__ == "__main__":
    asyncio.run(clean())
