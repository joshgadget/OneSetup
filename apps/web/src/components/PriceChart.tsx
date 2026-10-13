import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  IChartApi,
  IPriceLine,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  SeriesMarker,
  Time,
  UTCTimestamp,
} from "lightweight-charts";
import type { Candle, OverlaysResponse } from "../api";

interface Props {
  candles: Candle[];
  overlays?: OverlaysResponse | null;
  height?: number;
}

/* Caps keep the chart readable. The panel says how many items were found and how many
   of them are drawn, so nothing is silently hidden. */
export const OVERLAY_CAPS = {
  levels: 40,
  zones: 8,
  fvgs: 8,
  order_blocks: 8,
  pools: 12,
  msnr_levels: 10,
  markers: 80,
};

function toTime(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

function toSeries(candles: Candle[]) {
  return candles.map((c) => ({
    time: toTime(c.time),
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
  }));
}

function levelColor(kind: string): string {
  if (kind.includes("support")) return "#26a69a";
  if (kind.includes("resistance")) return "#ef5350";
  return "#90caf9";
}

function flowColor(direction: string): string {
  return direction === "long" || direction === "bullish" ? "#66bb6a" : "#ff8a65";
}

export default function PriceChart({ candles, overlays, height = 460 }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);

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
    markersRef.current = createSeriesMarkers(series, []);
    const onResize = () => chart.applyOptions({ width: containerRef.current?.clientWidth ?? undefined });
    window.addEventListener("resize", onResize);
    onResize();
    return () => {
      window.removeEventListener("resize", onResize);
      markersRef.current?.detach();
      markersRef.current = null;
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      linesRef.current = [];
    };
  }, [height]);

  useEffect(() => {
    if (!seriesRef.current) return;
    seriesRef.current.setData(toSeries(candles));
    chartRef.current?.timeScale().fitContent();
  }, [candles]);

  useEffect(() => {
    const series = seriesRef.current;
    const markersApi = markersRef.current;
    if (!series || !markersApi) return;

    for (const line of linesRef.current) series.removePriceLine(line);
    linesRef.current = [];

    const addLine = (price: number, color: string, title: string, style: LineStyle) => {
      if (!Number.isFinite(price)) return;
      linesRef.current.push(
        series.createPriceLine({
          price,
          color,
          title,
          lineWidth: 1,
          lineStyle: style,
          axisLabelVisible: false,
        })
      );
    };

    const markers: SeriesMarker<Time>[] = [];
    if (overlays) {
      for (const row of overlays.levels.slice(-OVERLAY_CAPS.levels)) {
        addLine(row.centre, levelColor(row.kind), row.kind, LineStyle.Dashed);
      }
      for (const row of overlays.msnr_levels.slice(-OVERLAY_CAPS.msnr_levels)) {
        addLine(row.price, "#ffd54f", `msnr ${row.kind}`, LineStyle.Dotted);
      }
      for (const row of overlays.zones.slice(-OVERLAY_CAPS.zones)) {
        const color = flowColor(row.kind);
        addLine(row.proximal, color, `${row.kind} proximal`, LineStyle.Solid);
        addLine(row.distal, color, `${row.kind} distal`, LineStyle.Dashed);
      }
      for (const row of overlays.fvgs.slice(-OVERLAY_CAPS.fvgs)) {
        const color = flowColor(row.direction);
        addLine(row.lower, color, `fvg ${row.direction}`, LineStyle.Dotted);
        addLine(row.upper, color, `fvg ${row.direction}`, LineStyle.Dotted);
      }
      for (const row of overlays.order_blocks.slice(-OVERLAY_CAPS.order_blocks)) {
        const color = flowColor(row.direction);
        addLine(row.lower, color, "ob", LineStyle.Dotted);
        addLine(row.upper, color, "ob", LineStyle.Dotted);
      }
      for (const row of overlays.pools.slice(-OVERLAY_CAPS.pools)) {
        addLine(row.level, "#b39ddb", `pool ${row.side}`, LineStyle.Dotted);
      }

      for (const row of overlays.mss) {
        markers.push({
          time: toTime(row.time),
          position: row.direction === "short" ? "aboveBar" : "belowBar",
          shape: row.direction === "short" ? "arrowDown" : "arrowUp",
          color: "#ffd54f",
          text: "MSS",
        });
      }
      for (const row of overlays.structure_events) {
        markers.push({
          time: toTime(row.time),
          position: row.direction === "short" ? "aboveBar" : "belowBar",
          shape: "circle",
          color: flowColor(row.direction),
          text: row.kind,
        });
      }
      for (const row of overlays.grabs) {
        markers.push({
          time: toTime(row.time),
          position: row.side === "sell_side" ? "belowBar" : "aboveBar",
          shape: "circle",
          color: "#b39ddb",
          text: "grab",
        });
      }
      for (const row of overlays.crt) {
        markers.push({
          time: toTime(row.time),
          position: row.direction === "short" ? "aboveBar" : "belowBar",
          shape: "square",
          color: "#4fc3f7",
          text: `CRT ${row.variant}`,
        });
      }
    }

    markers.sort((a, b) => (a.time as number) - (b.time as number));
    markersApi.setMarkers(markers.slice(-OVERLAY_CAPS.markers));
  }, [overlays]);

  return <div ref={containerRef} className="chart" style={{ height }} />;
}
