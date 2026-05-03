"""Quick test of data ingestion."""
from data_ingestion import fetch_ohlcv, compute_indicators, fetch_news
import json

# Test OHLCV
print("=== OHLCV Test ===")
df = fetch_ohlcv("RELIANCE.NS", period="5d", interval="1d")
print(f"OHLCV rows: {len(df)}")
if not df.empty:
    print(f"Columns: {list(df.columns)}")
    last = df.iloc[-1]
    print(f"Last close: {last.get('close', 'N/A')}")

# Test indicators
print("\n=== Indicators Test ===")
indicators = compute_indicators(df)
latest = indicators.get("latest", {})
print(f"Latest indicators: {json.dumps(latest, indent=2, default=str)}")

# Test news
print("\n=== News Test ===")
news = fetch_news("RELIANCE.NS")
print(f"News count: {len(news)}")
for n in news:
    print(f"  - {n.headline[:80]}")
