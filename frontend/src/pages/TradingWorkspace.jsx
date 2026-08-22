import React, { useState, useEffect } from 'react';
import TopNav from '../components/TopNav';
import LeftToolbar from '../components/LeftToolbar';
import RightPanel from '../components/RightPanel';
import InteractiveChart from '../components/InteractiveChart';
import BottomPanel from '../components/BottomPanel';
import ActionPill from '../components/ActionPill';
import MobileNav from '../components/MobileNav';
import { useApp } from '../context/AppContext';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { money } from '../format';

export default function TradingWorkspace() {
  const { 
    selectedTicker, 
    loadTicker, 
    marketData, 
    selectedAnalysis, 
    selectedTrades, 
    watchlistRows,
    runAnalysis,
    refreshMarket,
    isBusy
  } = useApp();
  
  const [timeframe, setTimeframe] = useState('1D');
  const [workspaceView, setWorkspaceView] = useState('chart'); // 'chart' or 'scanner'
  const navigate = useNavigate();
  
  const handleSymbolChange = (sym) => {
    if (sym === 'SEARCH') {
      const newSym = prompt("Enter Symbol:");
      if (newSym) {
        loadTicker(newSym.toUpperCase());
        navigate("/chart");
      }
    } else {
      loadTicker(sym);
      navigate("/chart");
    }
  };

  useEffect(() => {
    if (selectedTicker) {
      setWorkspaceView('chart');
    }
  }, [selectedTicker]);

  const location = useLocation();
  // Chart workspace lives at /chart and /analysis ("/" is now System B)
  const isChartView = location.pathname === '/chart' || location.pathname === '/analysis';
  const isInnerPage = !isChartView;

  return (
    <div className="trading-app-shell">
      {/* Top Navigation Bar */}
      <TopNav 
        currentSymbol={selectedTicker || "AAPL"} 
        onSymbolChange={handleSymbolChange} 
        onRunAnalysis={runAnalysis}
        onRefreshMarket={refreshMarket}
        isBusy={isBusy}
        timeframe={timeframe}
        onTimeframeChange={setTimeframe}
        view={workspaceView}
        onViewChange={setWorkspaceView}
      />
      
      {/* Main Workspace Area */}
      <div className="trading-workspace">
        
        {isInnerPage ? (
          <div className="page-content" style={{ flex: 1, overflowY: 'auto', padding: '20px', background: 'var(--bg)' }}>
            <Outlet />
          </div>
        ) : (
          <>
            {/* Left Toolbar for Drawings */}
            <LeftToolbar />
            
            {workspaceView === 'scanner' ? (
              <div className="scanner-view-container" style={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100%', overflowY: 'auto', padding: '20px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                  <div>
                    <h2 style={{ fontSize: '18px', fontWeight: 700, color: 'var(--text)' }}>Watchlist Scanner</h2>
                    <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Real-time indicators & predictive signals for all watchlisted assets</span>
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                    Total: <strong style={{ color: 'var(--text)' }}>{watchlistRows.length}</strong> stocks
                  </div>
                </div>
                <div className="scanner-grid">
                  {watchlistRows.map((row) => {
                    const sentimentVal = row.sentiment;
                    const mlPct = (row.ml * 100).toFixed(0);
                    return (
                      <div 
                        key={row.ticker} 
                        className={`scanner-card ${row.action.toLowerCase()}`}
                        onClick={() => {
                          loadTicker(row.ticker);
                          setWorkspaceView('chart');
                        }}
                      >
                        <div className="scanner-card-header">
                          <strong className="scanner-ticker">{row.ticker}</strong>
                          <ActionPill action={row.action} />
                        </div>
                        
                        <div className="scanner-card-body">
                          <div className="scanner-metric">
                            <span>Current Price</span>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                              <strong>{row.price ? money(row.price) : "--"}</strong>
                              {row.change_pct !== 0 && (
                                <span
                                  style={{
                                    fontSize: '10px',
                                    fontWeight: 700,
                                    fontFamily: 'JetBrains Mono, monospace',
                                    color: row.change_pct >= 0 ? 'var(--green)' : 'var(--red)',
                                    background: row.change_pct >= 0 ? 'var(--green-soft)' : 'var(--red-soft)',
                                    padding: '1px 5px',
                                    borderRadius: '4px',
                                  }}
                                >
                                  {row.change_pct >= 0 ? '+' : ''}{row.change_pct.toFixed(2)}%
                                </span>
                              )}
                            </div>
                          </div>
                          <div className="scanner-metric-row">
                            <div className="scanner-metric-half">
                              <span>ML Prediction</span>
                              <strong className={row.ml > 0.55 ? 'positive' : row.ml < 0.45 ? 'negative' : 'neutral'}>
                                {mlPct}%
                              </strong>
                            </div>
                            <div className="scanner-metric-half">
                              <span>Sentiment</span>
                              <strong className={sentimentVal > 0 ? 'positive' : sentimentVal < 0 ? 'negative' : 'neutral'}>
                                {sentimentVal > 0 ? "+" : ""}{sentimentVal.toFixed(2)}
                              </strong>
                            </div>
                          </div>
                        </div>
                        
                        <div className="scanner-card-footer">
                          <span>Click to open interactive chart</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : (
              /* Central Chart Area */
              <div className="main-chart-area" style={{ display: 'flex', flexDirection: 'column', height: '100%', flex: 1 }}>
                <div className="chart-container" style={{ flex: 1, minHeight: 0 }}>
                  <InteractiveChart 
                    ticker={selectedTicker || "AAPL"} 
                    bars={marketData?.bars || []} 
                    indicators={marketData?.indicators || {}} 
                    trades={selectedTrades || []} 
                    analysis={selectedAnalysis}
                    timeframe={timeframe}
                  />
                </div>
                {/* Bottom Trade History Panel */}
                <BottomPanel currentSymbol={selectedTicker || "AAPL"} />
              </div>
            )}
            
            {/* Right Panel for Watchlist and AI Analysis */}
            <RightPanel 
              currentSymbol={selectedTicker || "AAPL"} 
              onSymbolChange={handleSymbolChange}
              analysisData={selectedAnalysis}
              watchlistRows={watchlistRows}
              marketData={marketData}
            />
          </>
        )}
      </div>

      {/* Mobile Bottom Navigation (visible only ≤768px via CSS) */}
      <MobileNav />
    </div>
  );
}
