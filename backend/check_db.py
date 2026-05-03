import asyncio
from pymongo import AsyncMongoClient

async def main():
    c = AsyncMongoClient('REDACTED_USE_ENV_VAR')
    db = c['paper_trader']
    count = await db['analysis_log'].count_documents({})
    print(f'analysis_log has {count} docs')
    if count > 0:
        doc = await db['analysis_log'].find_one({}, {'_id': 0})
        print(doc)
    
    count2 = await db['market_data'].count_documents({})
    print(f'market_data has {count2} docs')

asyncio.run(main())
