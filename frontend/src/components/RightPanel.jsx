import React, { useEffect, useRef } from 'react';
import { Plus, ChevronDown, Activity, Zap } from 'lucide-react';
import gsap from 'gsap';
import ScoreGauge from './ScoreGauge';

export default function RightPanel({ currentSymbol, analysisData, onSymbolChange, watchlistRows, marketData }) {
  const watchlist = watchlistRows || [];
  
  const scoreRaw = analysisData?.final_score || 0;
  const scorePercent = (scoreRaw * 100).toFixed(0);
  const action = analysisData?.action || 'NEUTRAL';
  
  const mlConfidence = (analysisData?.ml_confidence || 0) * 100;
  const llmScore = (analysisData?.gemini_confidence || 0) * 100;
  const newsImpact = (analysisData?.gemini_sentiment_score || 0);
  
  const rsiRaw = marketData?.indicators?.rsi_14;
  const rsi = typeof rsiRaw === 'number' ? rsiRaw.toFixed(2) : '--';
  const macdVal = marketData?.indicators?.macd;
  const macdSignal = marketData?.indicators?.macd_signal;
  const macdStatus = (typeof macdVal === 'number' && typeof macdSignal === 'number') 
    ? (macdVal > macdSignal ? 'BUY' : macdVal < macdSignal ? 'SELL' : 'NEUTRAL') 
    : 'NEUTRAL';
  
  const closePrice = marketData?.indicators?.close;
  const sma20 = marketData?.indicators?.sma_20;
  const smaStatus = (typeof closePrice === 'number' && typeof sma20 === 'number')
    ? (closePrice > sma20 ? 'BUY' : 'SELL')
    : 'NEUTRAL';

  const panelRef = useRef(null);

  useEffect(() => {
    const ctx = gsap.context(() => {
      // Slide panel in from right
      gsap.fromTo(panelRef.current, 
        { x: 50, opacity: 0 },
        {
          x: 0,
          opacity: 1,
          duration: 0.6,
          ease: "power3.out"
        }
      );

      // Stagger watchlist items
      gsap.fromTo(".watchlist-row", 
        { x: 20, opacity: 0 },
        {
          x: 0,
          opacity: 1,
          duration: 0.4,
          stagger: 0.05,
          ease: "power2.out",
          delay: 0.2
        }
      );
    }, panelRef);
    
    return () => ctx.revert();
  }, [analysisData]);

  return (
    <div className="right-panel" ref={panelRef}>
      {/* Watchlist Header */}
      <div className="panel-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span>Watchlist</span>
          <ChevronDown size={14} color="var(--text-secondary)" />
        </div>
        <button title="Add Symbol" onClick={() => onSymbolChange?.('SEARCH')}>
          <Plus size={16} color="var(--text-secondary)" />
        </button>
      </div>

      {/* Watchlist Items */}
      <div className="watchlist-container">
        {watchlist.map(item => (
          <div 
            key={item.ticker} 
            className={`watchlist-row ${item.ticker === currentSymbol ? 'active' : ''}`}
            onClick={() => onSymbolChange?.(item.ticker)}
          >
            <span className="wl-sym">{item.ticker}</span>
            <div className="wl-price-col">
              <span className="wl-price">{Number(item.price).toFixed(2)}</span>
              <span className={`wl-change ${item.action === 'BUY' ? 'up' : item.action === 'SELL' ? 'down' : ''}`}>
                {item.action || 'HOLD'}
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* Quant Brain / AI Analysis Section */}
      <div className="quant-brain-section">
        <div className="panel-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Zap size={14} color="var(--accent)" />
            <span>Quant Brain</span>
          </div>
          <Activity size={14} color="var(--text-secondary)" />
        </div>
        
        {/* Animated ScoreGauges Grid */}
        <div className="score-grid" style={{ marginTop: "12px", marginBottom: "12px" }}>
          <ScoreGauge
            label="Final Score"
            value={Number(analysisData?.final_score || 0)}
            min={0}
            max={1}
            threshold={0.6}
          />
          <ScoreGauge
            label="LLM Conf"
            value={Number(analysisData?.gemini_confidence || 0)}
            min={0}
            max={1}
            threshold={0.6}
          />
          <ScoreGauge
            label="ML Prob"
            value={Number(analysisData?.ml_confidence || 0)}
            min={0}
            max={1}
            threshold={0.55}
          />
          <ScoreGauge
            label="News Sentiment"
            value={Number(analysisData?.gemini_sentiment_score || 0)}
            min={-1}
            max={1}
            threshold={0}
            sentimentMode
          />
        </div>

        {/* Signals Matrix */}
        <div className="signal-matrix">
          <div className="signal-cell">
            <span>Action</span>
            <strong className={action === 'BUY' ? 'positive' : action === 'SELL' ? 'negative' : 'neutral'}>
              {action}
            </strong>
          </div>
          <div className="signal-cell">
            <span>RSI (14)</span>
            <strong className={rsi > 70 ? 'negative' : rsi < 30 ? 'positive' : 'neutral'}>
              {rsi}
            </strong>
          </div>
          <div className="signal-cell">
            <span>MACD</span>
            <strong className={macdStatus === 'BUY' ? 'positive' : macdStatus === 'SELL' ? 'negative' : 'neutral'}>
              {macdStatus}
            </strong>
          </div>
          <div className="signal-cell">
            <span>Moving Averages</span>
            <strong className={smaStatus === 'BUY' ? 'positive' : smaStatus === 'SELL' ? 'negative' : 'neutral'}>
              {smaStatus}
            </strong>
          </div>
        </div>
      </div>
    </div>
  );
}
