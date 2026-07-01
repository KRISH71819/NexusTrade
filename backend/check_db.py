import asyncio
import os
from pymongo import AsyncMongoClient
from dotenv import load_dotenv

load_dotenv()

async def main():
    uri = os.getenv('MONGODB_URI', '')
    if not uri:
        raise RuntimeError("MONGODB_URI not set in .env")
    c = AsyncMongoClient(uri)
    db = c[os.getenv('MONGODB_DB_NAME', 'paper_trader')]
    count = await db['analysis_log'].count_documents({})
    print(f'analysis_log has {count} docs')
    if count > 0:
        doc = await db['analysis_log'].find_one({}, {'_id': 0})
        print(doc)
    
    count2 = await db['market_data'].count_documents({})
    print(f'market_data has {count2} docs')

asyncio.run(main())
