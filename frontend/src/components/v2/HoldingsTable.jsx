import { money, percent } from "../../format";

export default function HoldingsTable({ holdings }) {
  if (!holdings?.length) return <p className="v2-empty">No open positions.</p>;
  return (
    <div className="v2-table-wrapper">
      <table className="v2-table">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Qty</th>
            <th>Avg Price</th>
            <th>Current Price</th>
            <th>P&L</th>
          </tr>
        </thead>
        <tbody>
          {holdings.map((h) => {
            const pnl = (h.current_price - h.avg_price) * h.quantity;
            return (
              <tr key={h.ticker}>
                <td><strong>{h.ticker}</strong></td>
                <td>{h.quantity}</td>
                <td>{money(h.avg_price)}</td>
                <td>{money(h.current_price)}</td>
                <td className={pnl >= 0 ? "v2-positive" : "v2-negative"}>
                  {money(pnl)} ({percent(h.unrealized_pnl_pct)})
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
