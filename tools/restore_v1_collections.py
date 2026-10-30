"""One-shot: rename legacy_v1_* collections back to original V1 names.
Never touches ohlcv_history. Never deletes: a fresh re-initialized collection
under the original name is moved aside to tmp_fresh_<name> first."""
import asyncio, os, sys
from motor.motor_asyncio import AsyncIOMotorClient
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))
except Exception:
    pass

SACROSANCT = {"ohlcv_history"}

async def main():
    uri = os.environ.get("MONGODB_URI", "")
    if not uri:
        print("ABORT: MONGODB_URI not found"); sys.exit(1)
    client = AsyncIOMotorClient(uri)
    try:
        db = client.get_default_database()
    except Exception:
        db = client.get_database("paper_trader")
    names = set(await db.list_collection_names())
    legacy = sorted(n for n in names if n.startswith("legacy_v1_"))
    if not legacy:
        print("Nothing to restore."); return
    for n in legacy:
        orig = n[len("legacy_v1_"):]
        if orig in SACROSANCT:
            print(f"SKIP sacrosanct: {orig}"); continue
        if orig in names:
            await db[orig].rename("tmp_fresh_" + orig)
            print(f"  moved fresh {orig} -> tmp_fresh_{orig}")
        await db[n].rename(orig)
        print(f"  restored {n} -> {orig}")
    after = set(await db.list_collection_names())
    print("ohlcv_history present:", "ohlcv_history" in after)
    print("DONE. Delete tmp_fresh_* manually later when sure.")
    client.close()

if __name__ == "__main__":
    asyncio.run(main())
