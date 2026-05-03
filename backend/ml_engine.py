"""
ML Engine — XGBoost trend predictor for swing trading.
Dynamically trains on the stored 60-day hourly data to predict the next bar.
"""

import logging
import pandas as pd
import numpy as np
import xgboost as xgb
from database import get_market_data_collection

logger = logging.getLogger(__name__)


async def predict_trend(ticker: str, indicators: dict) -> dict:
    """
    Predict bullish/bearish trend using XGBoost on recent historical data.
    
    Returns:
        {
            "ml_confidence": float,  # 0.0-1.0 probability of bullish trend
            "features_used": dict,   # feature values used for prediction
        }
    """
    logger.info(f"Generating ML prediction for {ticker}")
    
    try:
        # Fetch historical bars from MongoDB
        collection = get_market_data_collection()
        doc = await collection.find_one({"ticker": ticker})
        
        if not doc or not doc.get("bars") or len(doc["bars"]) < 30:
            logger.warning(f"Not enough historical data for {ticker} to train ML. Returning neutral.")
            return {"ml_confidence": 0.50, "features_used": indicators if indicators else {}}
            
        df = pd.DataFrame(doc["bars"])
        
        # We need to recreate some features for training
        # Target: Is the next close higher than the current close?
        df['target'] = (df['close'].shift(-1) > df['close']).astype(int)
        
        # Features: RSI, MACD, BB width, etc. We will approximate them if not available in bars.
        # But for simplicity, we use basic price action features:
        df['returns'] = df['close'].pct_change()
        df['volatility'] = df['returns'].rolling(10).std()
        df['sma_20_dist'] = (df['close'] - df['close'].rolling(20).mean()) / df['close']
        df['momentum_5'] = df['close'] / df['close'].shift(5) - 1
        
        df = df.dropna()
        
        if len(df) < 20:
            return {"ml_confidence": 0.50, "features_used": indicators if indicators else {}}
            
        # Features for X
        feature_cols = ['returns', 'volatility', 'sma_20_dist', 'momentum_5']
        X = df[feature_cols]
        y = df['target']
        
        # Train a quick XGBoost model
        model = xgb.XGBClassifier(
            n_estimators=50,
            max_depth=3,
            learning_rate=0.1,
            use_label_encoder=False,
            eval_metric='logloss',
            random_state=42
        )
        model.fit(X, y)
        
        # Get the latest row to predict the *next* step
        latest_row = df.iloc[-1:][feature_cols]
        prob_bullish = float(model.predict_proba(latest_row)[0][1])
        
        return {
            "ml_confidence": prob_bullish,
            "features_used": {
                "returns": float(latest_row['returns'].iloc[0]),
                "sma_20_dist": float(latest_row['sma_20_dist'].iloc[0]),
                "momentum_5": float(latest_row['momentum_5'].iloc[0]),
            }
        }
        
    except Exception as e:
        logger.error(f"ML engine error for {ticker}: {e}")
        return {
            "ml_confidence": 0.50,
            "features_used": indicators if indicators else {},
        }
