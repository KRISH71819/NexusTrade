import { useEffect, useRef } from "react";
import { createChart } from "lightweight-charts";

export default function EquityCurve({ history }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current || !history?.length) return;

    const chart = createChart(containerRef.current, {
      height: 300,
      layout: {
        background: { color: "#09090b" },
        textColor: "#a1a1aa",
      },
      grid: {
        vertLines: { color: "#1f1f22" },
        horzLines: { color: "#1f1f22" },
      },
      timeScale: {
        borderColor: "#27272a",
        timeVisible: true,
      },
    });

    const series = chart.addAreaSeries({
      lineColor: "#d4a853",
      topColor: "rgba(212, 168, 83, 0.25)",
      bottomColor: "rgba(212, 168, 83, 0.0)",
    });

    const formattedData = history
      .map((h) => {
        let t;
        if (h.date) {
          t = h.date;
        } else if (h.timestamp) {
          t = Math.floor(new Date(h.timestamp).getTime() / 1000);
        } else {
          return null;
        }
        return {
          time: t,
          value: Number(h.total_equity ?? h.total_value ?? 0),
        };
      })
      .filter((d) => d && !isNaN(d.value) && d.value > 0)
      .sort((a, b) => (a.time > b.time ? 1 : -1));

    if (formattedData.length > 0) {
      series.setData(formattedData);
      chart.timeScale().fitContent();
    }

    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [history]);

  return <div ref={containerRef} style={{ width: "100%", height: 300 }} />;
}
