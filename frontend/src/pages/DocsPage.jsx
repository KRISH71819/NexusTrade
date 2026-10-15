import { BookOpen, Shield, TrendingUp, Layers } from "lucide-react";

export default function DocsPage() {
  return (
    <div className="v2-page">
      <header className="v2-header">
        <div>
          <h2>NexusTrade V2 Architecture & Specifications</h2>
          <p className="v2-subtitle">End-of-Day (EOD) Quantitative Portfolio Engine</p>
        </div>
      </header>

      <div className="v2-docs-content">
        <section className="v2-card v2-doc-section">
          <div className="v2-doc-title">
            <TrendingUp size={20} className="v2-accent" />
            <h3>1. Core Quantitative Strategy (System B)</h3>
          </div>
          <p>
            NexusTrade V2 operates on strictly End-of-Day (EOD) daily bars for the qualified NSE Indian equity universe (100–250 stocks with median 20d turnover &gt; ₹5 Cr).
          </p>
          <ul>
            <li><strong>Universe Selection:</strong> Filtered for liquidity, minimum 1,500 trading days, and price &gt; ₹50.</li>
            <li><strong>Rebalance Cadence:</strong> 60 trading days (~84 calendar days), or immediate rebalance on shift into/out of CRISIS.</li>
            <li><strong>Portfolio Size:</strong> Target Top-25 equal-weight portfolio.</li>
            <li><strong>Rank Buffers:</strong> Entry at Rank &le; 25, exit only if Rank &gt; 35 (prevents churn on boundary).</li>
            <li><strong>No-Trade Band:</strong> &plusmn;10% tolerance band around target weights prevents unnecessary micro-trades.</li>
            <li><strong>Cost-Aware Swap Skip:</strong> Drops swap if &Delta;Rank &lt; 5 between candidate and held stock.</li>
            <li><strong>Turnover Budget:</strong> Scales down non-mandatory drift adjustments by 30% if gross turnover exceeds 30%.</li>
          </ul>
        </section>

        <section className="v2-card v2-doc-section">
          <div className="v2-doc-title">
            <Shield size={20} className="v2-accent" />
            <h3>2. Regime Matrix & Downside Protection</h3>
          </div>
          <table className="v2-table">
            <thead>
              <tr>
                <th>Regime</th>
                <th>Conditions</th>
                <th>Target Exposure</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><span className="v2-badge v2-badge-danger">CRISIS</span></td>
                <td>Breadth &lt; 20% OR Index Gap &lt; -15%</td>
                <td>0%</td>
                <td>Liquidate all positions to cash</td>
              </tr>
              <tr>
                <td><span className="v2-badge v2-badge-warning">RISK_OFF</span></td>
                <td>Breadth &lt; 40% OR Index Gap &lt; -5%</td>
                <td>50%</td>
                <td>Scale down portfolio exposure</td>
              </tr>
              <tr>
                <td><span className="v2-badge v2-badge-neutral">NEUTRAL</span></td>
                <td>Breadth 40% – 70%</td>
                <td>80%</td>
                <td>Standard portfolio sizing</td>
              </tr>
              <tr>
                <td><span className="v2-badge v2-badge-success">RISK_ON</span></td>
                <td>Breadth &gt; 70% AND Index Gap &gt; 0%</td>
                <td>100%</td>
                <td>Fully deployed in top-ranked alphas</td>
              </tr>
            </tbody>
          </table>
          <p style={{ marginTop: "1rem" }}>
            <strong>Catastrophic Stops:</strong> Every holding is checked at 15:32 IST daily. Any holding dropping &ge;25% from weighted-average cost basis is immediately flagged for Market-on-Close liquidation regardless of rank or market regime.
          </p>
        </section>

        <section className="v2-card v2-doc-section">
          <div className="v2-doc-title">
            <Layers size={20} className="v2-accent" />
            <h3>3. Mathematical Alpha Sleeves</h3>
          </div>
          <p>The portfolio allocates across validated, orthogonal mathematical sleeves:</p>
          <ul>
            <li><strong>Sleeve M (Momentum):</strong> 200-day trend momentum rank (deployed day-1).</li>
            <li><strong>Sleeve D (Defensive):</strong> Low-volatility filter (negated 120-day volatility among trend-positive names).</li>
            <li><strong>Sleeve R (Mean Reversion):</strong> 21-day oversold mean reversion rank.</li>
            <li><strong>Sleeve V (Volatility Momentum):</strong> 60-day volatility-adjusted momentum.</li>
          </ul>
        </section>

        <section className="v2-card v2-doc-section">
          <div className="v2-doc-title">
            <BookOpen size={20} className="v2-accent" />
            <h3>4. Operational Constraints</h3>
          </div>
          <ul>
            <li><strong>Free Tier Infrastructure:</strong> Hugging Face Spaces free CPU + MongoDB Atlas M0 (512MB hard limit).</li>
            <li><strong>Jev AI Hard Budget:</strong> $2.00/month maximum token expenditure; fail-closed behavior when unavailable.</li>
            <li><strong>No Intraday Sockets:</strong> 60-second polling replaces battery-draining WebSocket loops.</li>
          </ul>
        </section>
      </div>
    </div>
  );
}
