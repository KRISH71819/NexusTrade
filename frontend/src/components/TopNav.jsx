import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Search, SlidersHorizontal, Maximize, Briefcase, ClipboardList, Activity, RefreshCw, CandlestickChart, LayoutDashboard } from 'lucide-react';

export default function TopNav({ currentSymbol = "AAPL", onSymbolChange, onRunAnalysis, onRefreshMarket, isBusy, timeframe, onTimeframeChange, view = 'chart', onViewChange }) {
  const location = useLocation();
  
  return (
    <div className="topnav">
      <div className="topnav-section">
        {/* Brand / Logo */}
        <Link to="/" style={{ textDecoration: 'none', color: 'inherit' }}>
          <div className="brand-terminal" style={{ cursor: 'pointer' }}>
            <div className="brand-mark-terminal">NX</div>
            <strong>NexusTrade</strong>
          </div>
        </Link>
        
        <div className="divider" />
        
        {/* Symbol Search */}
        <div className="symbol-search" onClick={() => onSymbolChange?.('SEARCH')}>
          <Search size={16} color="var(--text-secondary)" />
          <span>{currentSymbol}</span>
        </div>
        
        <div className="divider" />
        
        {/* View Toggle (Chart / Scanner) */}
        {(location.pathname === '/' || location.pathname === '/analysis') && (
          <div className="segmented" style={{ marginRight: '12px' }}>
            <button 
              className={`top-btn ${view === 'chart' ? 'active' : ''}`}
              onClick={() => onViewChange?.('chart')}
              type="button"
            >
              Chart
            </button>
            <button 
              className={`top-btn ${view === 'scanner' ? 'active' : ''}`}
              onClick={() => onViewChange?.('scanner')}
              type="button"
            >
              Scanner
            </button>
          </div>
        )}
        
        {/* Timeframes */}
        <div className="topnav-section" style={{ gap: '4px' }}>
          {['1D', '1W', '1M', '3M', '1Y'].map(tf => (
            <button 
              key={tf} 
              className={`top-btn ${tf === timeframe ? 'active' : ''}`}
              onClick={() => onTimeframeChange?.(tf)}
              type="button"
            >
              {tf}
            </button>
          ))}
        </div>
        
        <div className="divider" />
        
        {/* Chart Types */}
        <button className="top-btn active" type="button">
          <CandlestickChart size={18} />
        </button>
      </div>

      <div className="topnav-section">
        {/* Run Analysis Button */}
        <button 
          className="top-btn" 
          title="Run Analysis" 
          onClick={onRunAnalysis}
          disabled={isBusy}
          style={{ opacity: isBusy ? 0.5 : 1, padding: '0 12px', gap: '6px', width: 'auto' }}
          type="button"
        >
          <Activity size={16} color="var(--accent)" />
          <span style={{ fontSize: '12px', fontWeight: 600 }}>Analyze</span>
        </button>

        {/* Refresh Market Button */}
        <button 
          className="top-btn" 
          title="Refresh Market Data" 
          onClick={onRefreshMarket}
          disabled={isBusy}
          style={{ opacity: isBusy ? 0.5 : 1, padding: '0 12px', gap: '6px', width: 'auto' }}
          type="button"
        >
          <RefreshCw size={16} className={isBusy ? 'spin' : ''} />
          <span style={{ fontSize: '12px', fontWeight: 600 }}>Refresh</span>
        </button>

        <div className="divider" />
        
        {/* Action icons */}
        <Link 
          to="/dashboard" 
          className={`top-btn ${location.pathname === '/dashboard' ? 'active' : ''}`} 
          title="Dashboard" 
          style={{ display: 'grid', placeItems: 'center', color: 'inherit' }}
        >
          <LayoutDashboard size={18} />
        </Link>
        <Link 
          to="/trades" 
          className={`top-btn ${location.pathname === '/trades' ? 'active' : ''}`} 
          title="Trade History" 
          style={{ display: 'grid', placeItems: 'center', color: 'inherit' }}
        >
          <ClipboardList size={18} />
        </Link>
        <Link 
          to="/trading" 
          className={`top-btn ${location.pathname === '/trading' ? 'active' : ''}`} 
          title="Controls & Settings" 
          style={{ display: 'grid', placeItems: 'center', color: 'inherit' }}
        >
          <SlidersHorizontal size={18} />
        </Link>
        <button className="top-btn" title="Fullscreen" onClick={() => {
          if (!document.fullscreenElement) {
            document.documentElement.requestFullscreen();
          } else {
            if (document.exitFullscreen) document.exitFullscreen();
          }
        }} type="button">
          <Maximize size={18} />
        </button>
        <div className="divider" />
        <Link 
          to="/portfolio" 
          className={`top-btn ${location.pathname === '/portfolio' ? 'active' : ''}`} 
          title="Portfolio" 
          style={{ display: 'grid', placeItems: 'center', color: 'inherit' }}
        >
          <Briefcase size={18} />
        </Link>
      </div>
    </div>
  );
}
