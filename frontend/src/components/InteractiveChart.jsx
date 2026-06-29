import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { CrosshairMode, createChart } from "lightweight-charts";
import { Maximize2, Minus, Plus, RotateCcw, Radio } from "lucide-react";
import { compactMoney, dateTime, money, volume } from "../format";
import { useApp } from "../context/AppContext";

const INTERVALS = [
  { key: "1m", label: "1m" },
  { key: "5m", label: "5m" },
  { key: "15m", label: "15m" },
  { key: "1h", label: "1H" },
  { key: "1d", label: "1D" },
];

export default function InteractiveChart({ analysis, bars, indicators, ticker, trades, timeframe }) {
  const containerRef = useRef(null);
  const rsiContainerRef = useRef(null);
  const chartRef = useRef(null);
  const rsiChartRef = useRef(null);
  const candleRef = useRef(null);
  const volumeRef = useRef(null);
  const fastMaRef = useRef(null);
  const slowMaRef = useRef(null);
  const rsiRef = useRef(null);
  const priceLineRef = useRef(null);
  const stopLossLineRef = useRef(null);
  const trailingStopLineRef = useRef(null);
  const resizeObserverRef = useRef(null);
  const [hover, setHover] = useState(null);
  const [activeInterval, setActiveInterval] = useState("1h");

  const { realtime } = useApp();
  const { prices, candles: liveCandles, feedConnected, subscribe, setChartInterval } = realtime;

  const chartData = useMemo(() => normalizeBars(bars), [bars]);
  const lastBar = chartData.candles[chartData.candles.length - 1] || null;
  const liveTick = prices[ticker] || null;

  // Subscribe to ticker for real-time updates
  useEffect(() => {
    if (ticker) {
      subscribe(ticker, activeInterval);
    }
  }, [ticker, activeInterval, subscribe]);

  // Handle interval change
  const onIntervalChange = useCallback(
    (interval) => {
      setActiveInterval(interval);
      if (ticker) {
        setChartInterval(ticker, interval);
      }
    },
    [ticker, setChartInterval]
  );

  // Create the main price chart
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
        vertLines: { color: "rgba(39, 39, 42, 0.75)" },
        horzLines: { color: "rgba(39, 39, 42, 0.75)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "rgba(161, 161, 170, 0.4)", labelBackgroundColor: "#27272a" },
        horzLine: { color: "rgba(161, 161, 170, 0.4)", labelBackgroundColor: "#27272a" },
      },
      rightPriceScale: {
        borderColor: "#27272a",
        scaleMargins: { top: 0.08, bottom: 0.24 },
      },
      timeScale: {
        borderColor: "#27272a",
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
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderUpColor: "#22c55e",
      borderDownColor: "#ef4444",
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
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
      color: "#ffffff",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "SMA 10",
    });
    const slowMa = chart.addLineSeries({
      color: "#ff9100",
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
      if (candle) {
        const change = candle.close - candle.open;
        const changePct = candle.open ? (change / candle.open) * 100 : 0;
        setHover({
          time: param.time,
          ...candle,
          volume: vol?.value || 0,
          change,
          changePct,
        });
      }
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

  // Create RSI sub-chart
  useEffect(() => {
    if (!rsiContainerRef.current) return;

    const rsiChart = createChart(rsiContainerRef.current, {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: "#787b86",
        fontFamily: "Inter, Segoe UI, sans-serif",
        fontSize: 10,
      },
      grid: {
        vertLines: { color: "rgba(39, 39, 42, 0.5)" },
        horzLines: { color: "rgba(39, 39, 42, 0.5)" },
      },
      rightPriceScale: {
        borderColor: "#27272a",
        scaleMargins: { top: 0.05, bottom: 0.05 },
      },
      timeScale: { visible: false },
      crosshair: {
        vertLine: { visible: false },
        horzLine: { color: "rgba(161, 161, 170, 0.4)", labelBackgroundColor: "#27272a" },
      },
      handleScroll: false,
      handleScale: false,
    });

    const rsiSeries = rsiChart.addLineSeries({
      color: "var(--accent)",
      lineWidth: 1.5,
      priceLineVisible: false,
      lastValueVisible: true,
      title: "RSI 14",
      priceFormat: { type: "price", precision: 1, minMove: 0.1 },
    });

    // Overbought/oversold lines
    const obLine = rsiChart.addLineSeries({
      color: "rgba(242, 54, 69, 0.3)",
      lineWidth: 1,
      lineStyle: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const osLine = rsiChart.addLineSeries({
      color: "rgba(8, 153, 129, 0.3)",
      lineWidth: 1,
      lineStyle: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    rsiChartRef.current = rsiChart;
    rsiRef.current = { series: rsiSeries, obLine, osLine };

    return () => {
      rsiChart.remove();
      rsiChartRef.current = null;
      rsiRef.current = null;
    };
  }, []);

  // Set data when bars/analysis/trades change
  useEffect(() => {
    if (!chartRef.current || !candleRef.current || !volumeRef.current) return;

    candleRef.current.setData(chartData.candles);
    volumeRef.current.setData(chartData.volumes);
    fastMaRef.current?.setData(movingAverage(chartData.candles, 10));
    slowMaRef.current?.setData(movingAverage(chartData.candles, 20));
    candleRef.current.setMarkers(buildMarkers(trades, analysis));
    chartRef.current.timeScale().fitContent();

    // RSI data
    if (rsiRef.current && chartData.candles.length > 14) {
      const rsiData = computeRSI(chartData.candles, 14);
      rsiRef.current.series.setData(rsiData);

      // Set OB/OS lines spanning the full time range
      if (rsiData.length >= 2) {
        const first = rsiData[0].time;
        const last = rsiData[rsiData.length - 1].time;
        rsiRef.current.obLine.setData([
          { time: first, value: 70 },
          { time: last, value: 70 },
        ]);
        rsiRef.current.osLine.setData([
          { time: first, value: 30 },
          { time: last, value: 30 },
        ]);
      }
      rsiChartRef.current?.timeScale().fitContent();
    }
  }, [analysis, chartData, trades]);

  // Live candle updates from WebSocket
  useEffect(() => {
    if (!candleRef.current || !volumeRef.current || !liveCandles?.length) return;

    for (const c of liveCandles) {
      if (c.ticker !== ticker) continue;
      // Update last candle
      const candleTime = c.time;
      if (candleTime) {
        candleRef.current.update({
          time: candleTime,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        });
        volumeRef.current.update({
          time: candleTime,
          value: c.volume || 0,
          color:
            c.close >= c.open
              ? "rgba(8, 153, 129, 0.38)"
              : "rgba(242, 54, 69, 0.38)",
        });
      }
    }
  }, [liveCandles, ticker]);

  // Live price line
  useEffect(() => {
    if (!candleRef.current || !liveTick?.ltp) return;

    // Remove old price line
    if (priceLineRef.current) {
      try { candleRef.current.removePriceLine(priceLineRef.current); } catch {}
    }

    priceLineRef.current = candleRef.current.createPriceLine({
      price: liveTick.ltp,
      color: "var(--accent)",
      lineWidth: 1,
      lineStyle: 2, // dashed
      axisLabelVisible: true,
      title: "LTP",
    });
  }, [liveTick?.ltp]);

  // Set chart visible range when timeframe or chartData changes
  useEffect(() => {
    if (!chartRef.current || chartData.candles.length === 0) return;
    const timeScale = chartRef.current.timeScale();
    const lastCandle = chartData.candles[chartData.candles.length - 1];
    const lastTime = lastCandle.time;
    
    let fromTime;
    const oneDay = 24 * 60 * 60;
    
    switch (timeframe) {
      case '1D':
        fromTime = lastTime - oneDay;
        break;
      case '1W':
        fromTime = lastTime - 7 * oneDay;
        break;
      case '1M':
        fromTime = lastTime - 30 * oneDay;
        break;
      case '3M':
        fromTime = lastTime - 90 * oneDay;
        break;
      case '1Y':
        fromTime = lastTime - 365 * oneDay;
        break;
      default:
        fromTime = lastTime - 30 * oneDay;
    }
    
    const firstCandle = chartData.candles[0];
    if (fromTime < firstCandle.time) {
      fromTime = firstCandle.time;
    }
    
    setTimeout(() => {
      if (chartRef.current) {
        try {
          chartRef.current.timeScale().setVisibleRange({
            from: fromTime,
            to: lastTime,
          });
        } catch (e) {
          // ignore scale errors
        }
      }
    }, 50);
  }, [timeframe, chartData.candles]);

  const info = hover || (liveTick?.ltp ? { ...lastBar, close: liveTick.ltp } : lastBar);
  const hasBars = chartData.candles.length > 0;
  const change = info ? (info.change ?? (info.close - (info.open || info.close))) : 0;
  const changePct = info?.changePct ?? (info?.open ? (change / info.open) * 100 : 0);

  return (
    <div className="chart-experience">
      {/* Interval Switcher + OHLCV Toolbar */}
      <div className="chart-toolbar">
        <div className="interval-switcher">
          {INTERVALS.map((iv) => (
            <button
              key={iv.key}
              className={`interval-btn ${activeInterval === iv.key ? "active" : ""}`}
              onClick={() => onIntervalChange(iv.key)}
              type="button"
            >
              {iv.label}
            </button>
          ))}
          {feedConnected && (
            <span className="live-badge">
              <Radio size={10} className="pulse-icon" />
              LIVE
            </span>
          )}
        </div>

        <div className="chart-ohlcv-row">
          <div className="price-stack">
            <span>{ticker?.replace(".NS", "") || "No ticker"}</span>
            <strong className={change >= 0 ? "positive" : "negative"}>
              {info ? money(info.close) : "--"}
            </strong>
          </div>
          <ChartStat label="O" value={info ? money(info.open) : "--"} />
          <ChartStat label="H" value={info ? money(info.high) : "--"} />
          <ChartStat label="L" value={info ? money(info.low) : "--"} />
          <ChartStat label="C" value={info ? money(info.close) : "--"} />
          <ChartStat
            label="Chg"
            value={
              Number.isFinite(changePct)
                ? `${changePct >= 0 ? "+" : ""}${changePct.toFixed(2)}%`
                : "--"
            }
            className={change >= 0 ? "positive" : "negative"}
          />
          <ChartStat label="Vol" value={info ? volume(info.volume) : "--"} />
          <div className="chart-actions">
            <button onClick={() => zoomChart(chartRef.current, 0.72)} title="Zoom in" type="button">
              <Plus size={15} />
            </button>
            <button onClick={() => zoomChart(chartRef.current, 1.35)} title="Zoom out" type="button">
              <Minus size={15} />
            </button>
            <button onClick={() => chartRef.current?.timeScale().scrollToRealTime()} title="Latest" type="button">
              <RotateCcw size={15} />
            </button>
            <button onClick={() => chartRef.current?.timeScale().fitContent()} title="Fit" type="button">
              <Maximize2 size={15} />
            </button>
          </div>
        </div>
      </div>

      {/* Main Price Chart */}
      <div className="chart-canvas-wrap">
        <div className="chart-canvas" ref={containerRef} />
        {!hasBars && (
          <div className="chart-empty">
            <strong>No OHLCV bars for {ticker || "this ticker"}</strong>
            <span>Use Refresh Market or run analysis to populate data.</span>
          </div>
        )}
      </div>

      {/* RSI Sub-Chart */}
      <div className="chart-sub-panel">
        <div className="sub-panel-label">RSI (14)</div>
        <div className="chart-sub-canvas" ref={rsiContainerRef} />
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════════
   UTILITY FUNCTIONS
   ═══════════════════════════════════════════════════════════════════════════════ */

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
      candle: { time, timestamp: bar.timestamp, open, high, low, close },
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

function computeRSI(candles, period = 14) {
  if (candles.length < period + 1) return [];
  const rsiData = [];
  let avgGain = 0;
  let avgLoss = 0;

  // Calculate initial average gain/loss
  for (let i = 1; i <= period; i++) {
    const change = candles[i].close - candles[i - 1].close;
    if (change >= 0) avgGain += change;
    else avgLoss -= change;
  }
  avgGain /= period;
  avgLoss /= period;

  const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
  rsiData.push({
    time: candles[period].time,
    value: 100 - 100 / (1 + rs),
  });

  // Calculate remaining RSI values using Wilder's smoothing
  for (let i = period + 1; i < candles.length; i++) {
    const change = candles[i].close - candles[i - 1].close;
    const gain = change >= 0 ? change : 0;
    const loss = change < 0 ? -change : 0;

    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;

    const currentRs = avgLoss === 0 ? 100 : avgGain / avgLoss;
    rsiData.push({
      time: candles[i].time,
      value: 100 - 100 / (1 + currentRs),
    });
  }

  return rsiData;
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
        color: isBuy ? "#22c55e" : "#ef4444",
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
      color: analysis.action === "BUY" ? "#22c55e" : "#ef4444",
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
  timeScale.setVisibleLogicalRange({ from: center - half, to: center + half });
}

function ChartStat({ label, value, className = "" }) {
  return (
    <div className={`chart-stat ${className}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
