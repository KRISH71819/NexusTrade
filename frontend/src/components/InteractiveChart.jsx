import { useEffect, useMemo, useRef, useState } from "react";
import { CrosshairMode, createChart } from "lightweight-charts";
import { Maximize2, Minus, Plus, RotateCcw } from "lucide-react";
import { compactMoney, dateTime, money, volume } from "../format";

export default function InteractiveChart({ analysis, bars, indicators, ticker, trades }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const candleRef = useRef(null);
  const volumeRef = useRef(null);
  const fastMaRef = useRef(null);
  const slowMaRef = useRef(null);
  const resizeObserverRef = useRef(null);
  const [hover, setHover] = useState(null);

  const chartData = useMemo(() => normalizeBars(bars), [bars]);
  const lastBar = chartData.candles[chartData.candles.length - 1] || null;

  useEffect(() => {
    if (!containerRef.current) return undefined;

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: "#787b86",
        fontFamily: "Inter, Segoe UI, sans-serif",
      },
      grid: {
        vertLines: { color: "rgba(42, 46, 57, 0.75)" },
        horzLines: { color: "rgba(42, 46, 57, 0.75)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "rgba(41, 98, 255, 0.8)", labelBackgroundColor: "#2962ff" },
        horzLine: { color: "rgba(41, 98, 255, 0.8)", labelBackgroundColor: "#2962ff" },
      },
      rightPriceScale: {
        borderColor: "#2a2e39",
        scaleMargins: { top: 0.08, bottom: 0.24 },
      },
      timeScale: {
        borderColor: "#2a2e39",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 8,
        barSpacing: 9,
        minBarSpacing: 3,
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: true,
      },
      handleScale: {
        axisPressedMouseMove: true,
        mouseWheel: true,
        pinch: true,
      },
      localization: {
        priceFormatter: (price) => compactMoney(price),
      },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#089981",
      downColor: "#f23645",
      borderUpColor: "#089981",
      borderDownColor: "#f23645",
      wickUpColor: "#089981",
      wickDownColor: "#f23645",
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });
    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "",
      color: "rgba(120, 123, 134, 0.36)",
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.78, bottom: 0 },
    });

    const fastMa = chart.addLineSeries({
      color: "#2962ff",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "SMA 10",
    });
    const slowMa = chart.addLineSeries({
      color: "#f0b90b",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "SMA 20",
    });

    chart.subscribeCrosshairMove((param) => {
      if (!param?.time || !param.seriesData?.get(candleSeries)) {
        setHover(null);
        return;
      }
      const candle = param.seriesData.get(candleSeries);
      const vol = param.seriesData.get(volumeSeries);
      setHover({ time: param.time, ...candle, volume: vol?.value || 0 });
    });

    chartRef.current = chart;
    candleRef.current = candleSeries;
    volumeRef.current = volumeSeries;
    fastMaRef.current = fastMa;
    slowMaRef.current = slowMa;

    resizeObserverRef.current = new ResizeObserver(() => chart.timeScale().fitContent());
    resizeObserverRef.current.observe(containerRef.current);

    return () => {
      resizeObserverRef.current?.disconnect();
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
      fastMaRef.current = null;
      slowMaRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!chartRef.current || !candleRef.current || !volumeRef.current) return;

    candleRef.current.setData(chartData.candles);
    volumeRef.current.setData(chartData.volumes);
    fastMaRef.current?.setData(movingAverage(chartData.candles, 10));
    slowMaRef.current?.setData(movingAverage(chartData.candles, 20));
    candleRef.current.setMarkers(buildMarkers(trades, analysis));
    chartRef.current.timeScale().fitContent();
  }, [analysis, chartData, trades]);

  const info = hover || lastBar;
  const hasBars = chartData.candles.length > 0;

  return (
    <div className="chart-experience">
      <div className="chart-toolbar">
        <div className="price-stack">
          <span>{ticker || "No ticker selected"}</span>
          <strong>{info ? money(info.close) : "--"}</strong>
        </div>
        <ChartStat label="O" value={info ? money(info.open) : "--"} />
        <ChartStat label="H" value={info ? money(info.high) : "--"} />
        <ChartStat label="L" value={info ? money(info.low) : "--"} />
        <ChartStat label="C" value={info ? money(info.close) : "--"} />
        <ChartStat label="Vol" value={info ? volume(info.volume) : "--"} />
        <ChartStat label="RSI" value={formatIndicator(indicators.rsi || indicators.RSI)} />
        <ChartStat label="Updated" value={dateTime(info?.timestamp || info?.time)} />
        <div className="chart-actions">
          <button onClick={() => zoomChart(chartRef.current, 0.72)} title="Zoom in" type="button">
            <Plus size={15} />
          </button>
          <button onClick={() => zoomChart(chartRef.current, 1.35)} title="Zoom out" type="button">
            <Minus size={15} />
          </button>
          <button onClick={() => chartRef.current?.timeScale().scrollToRealTime()} title="Go to latest" type="button">
            <RotateCcw size={15} />
          </button>
          <button onClick={() => chartRef.current?.timeScale().fitContent()} title="Fit all candles" type="button">
            <Maximize2 size={15} />
          </button>
        </div>
      </div>

      <div className="chart-canvas-wrap">
        <div className="chart-canvas" ref={containerRef} />
        {!hasBars && (
          <div className="chart-empty">
            <strong>No OHLCV bars returned for {ticker || "this ticker"}</strong>
            <span>Use Refresh market or run the backend ingestion cycle to populate market_data.</span>
          </div>
        )}
      </div>
    </div>
  );
}

function normalizeBars(bars) {
  const byTime = new Map();

  bars.forEach((bar) => {
    const time = toChartTime(bar.timestamp);
    if (!time) return;
    const open = Number(bar.open);
    const high = Number(bar.high);
    const low = Number(bar.low);
    const close = Number(bar.close);
    const value = Number(bar.volume || 0);
    if (![open, high, low, close].every(Number.isFinite)) return;

    byTime.set(time, {
      candle: {
        time,
        timestamp: bar.timestamp,
        open,
        high,
        low,
        close,
      },
      volume: {
        time,
        value,
        color: close >= open ? "rgba(8, 153, 129, 0.38)" : "rgba(242, 54, 69, 0.38)",
      },
    });
  });

  const sorted = [...byTime.values()].sort((a, b) => a.candle.time - b.candle.time);
  return {
    candles: sorted.map((item) => item.candle),
    volumes: sorted.map((item) => item.volume),
  };
}

function movingAverage(candles, period) {
  if (candles.length < period) return [];

  const result = [];
  let sum = 0;
  candles.forEach((candle, index) => {
    sum += candle.close;
    if (index >= period) sum -= candles[index - period].close;
    if (index >= period - 1) {
      result.push({ time: candle.time, value: sum / period });
    }
  });
  return result;
}

function buildMarkers(trades, analysis) {
  const tradeMarkers = trades
    .map((trade) => {
      const time = toChartTime(trade.timestamp);
      if (!time) return null;
      const isBuy = trade.action === "BUY";
      return {
        time,
        position: isBuy ? "belowBar" : "aboveBar",
        color: isBuy ? "#089981" : "#f23645",
        shape: isBuy ? "arrowUp" : "arrowDown",
        text: `${trade.action} ${trade.quantity || ""}`.trim(),
      };
    })
    .filter(Boolean);

  const analysisTime = toChartTime(analysis?.timestamp);
  if (analysisTime && analysis?.action && analysis.action !== "HOLD") {
    tradeMarkers.push({
      time: analysisTime,
      position: analysis.action === "BUY" ? "belowBar" : "aboveBar",
      color: analysis.action === "BUY" ? "#089981" : "#f23645",
      shape: analysis.action === "BUY" ? "arrowUp" : "arrowDown",
      text: `AI ${analysis.action}`,
    });
  }

  return tradeMarkers.sort((a, b) => a.time - b.time);
}

function toChartTime(value) {
  if (!value) return null;
  if (typeof value === "number") return Math.floor(value > 10000000000 ? value / 1000 : value);
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return Math.floor(date.getTime() / 1000);
}

function zoomChart(chart, factor) {
  if (!chart) return;
  const timeScale = chart.timeScale();
  const range = timeScale.getVisibleLogicalRange();
  if (!range) return;

  const center = (range.from + range.to) / 2;
  const half = ((range.to - range.from) * factor) / 2;
  timeScale.setVisibleLogicalRange({
    from: center - half,
    to: center + half,
  });
}

function ChartStat({ label, value }) {
  return (
    <div className="chart-stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function formatIndicator(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "--";
}
