import { useEffect, useRef } from "react";
import { CandlestickSeries, createChart, IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import type { Candle } from "../api";

interface Props {
  candles: Candle[];
  height?: number;
}

function toSeries(candles: Candle[]) {
  return candles.map((c) => ({
    time: Math.floor(new Date(c.time).getTime() / 1000) as UTCTimestamp,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
  }));
}

export default function PriceChart({ candles, height = 460 }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      height,
      layout: { background: { color: "#0f1115" }, textColor: "#d1d4dc" },
      grid: { vertLines: { color: "#1c1f26" }, horzLines: { color: "#1c1f26" } },
      rightPriceScale: { borderColor: "#2a2e39" },
      timeScale: { borderColor: "#2a2e39", timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderVisible: false,
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350",
    });
    chartRef.current = chart;
    seriesRef.current = series;
    const onResize = () => chart.applyOptions({ width: containerRef.current?.clientWidth ?? undefined });
    window.addEventListener("resize", onResize);
    onResize();
    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [height]);

  useEffect(() => {
    if (!seriesRef.current) return;
    seriesRef.current.setData(toSeries(candles));
    chartRef.current?.timeScale().fitContent();
  }, [candles]);

  return <div ref={containerRef} className="chart" style={{ height }} />;
}