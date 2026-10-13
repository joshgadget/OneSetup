import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
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

export interface OverlayVisibility {
  levels: boolean;
  zones: boolean;
  fvgs: boolean;
  orderBlocks: boolean;
  pools: boolean;
  msnr: boolean;
  structure: boolean;
  crt: boolean;
}

export const DEFAULT_VISIBILITY: OverlayVisibility = {
  levels: true,
  zones: true,
  fvgs: true,
  orderBlocks: true,
  pools: true,
  msnr: false,
  structure: true,
  crt: true,
};

interface Props {
  candles: Candle[];
  overlays?: OverlaysResponse | null;
  height?: number;
  fitKey?: string;
  visible?: OverlayVisibility;
  showVolume?: boolean;
  showAtr?: boolean;
}

/* Caps keep the chart readable. The legend under the chart says how many items were
   found and how many are drawn, so nothing is silently hidden. */
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

export default function PriceChart({
  candles,
  overlays,
  height = 520,
  fitKey = "",
  visible = DEFAULT_VISIBILITY,
  showVolume = true,
  showAtr = true,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const atrRef = useRef<ISeriesApi<"Line"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);
  const lastFitRef = useRef<string | null>(null);
  const rangeRef = useRef<{ from: number; to: number } | null>(null);

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
    if (showVolume) {
      volumeRef.current = chart.addSeries(
        HistogramSeries,
        { priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "volume" } },
        1
      );
    } else {
      volumeRef.current = null;
    }
    if (showAtr) {
      atrRef.current = chart.addSeries(
        LineSeries,
        { color: "#ffd54f", lineWidth: 1, priceLineVisible: false, lastValueVisible: true },
        2
      );
    } else {
      atrRef.current = null;
    }
    const panes = chart.panes();
    if (showVolume && panes[1]) panes[1].setHeight(90);
    if (showAtr && panes[showVolume ? 2 : 1]) panes[showVolume ? 2 : 1].setHeight(80);
    if (rangeRef.current) {
      chart.timeScale().setVisibleLogicalRange(rangeRef.current);
    }
    const rememberRange = () => {
      const range = chart.timeScale().getVisibleLogicalRange();
      if (range) rangeRef.current = { from: range.from, to: range.to };
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(rememberRange);
    const onResize = () => chart.applyOptions({ width: containerRef.current?.clientWidth ?? undefined });
    window.addEventListener("resize", onResize);
    onResize();
    return () => {
      window.removeEventListener("resize", onResize);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(rememberRange);
      markersRef.current?.detach();
      markersRef.current = null;
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      volumeRef.current = null;
      atrRef.current = null;
      linesRef.current = [];
    };
  }, [height, showVolume, showAtr]);

  useEffect(() => {
    if (!seriesRef.current) return;
    seriesRef.current.setData(toSeries(candles));
    if (volumeRef.current) {
      volumeRef.current.setData(
        candles.map((c) => ({
          time: toTime(c.time),
          value: c.volume ?? 0,
          color: c.close >= c.open ? "rgba(38,166,154,0.5)" : "rgba(239,83,80,0.5)",
        }))
      );
    }
    if (lastFitRef.current !== fitKey) {
      lastFitRef.current = fitKey;
      chartRef.current?.timeScale().fitContent();
      rangeRef.current = null;
    }
  }, [candles, fitKey]);

  useEffect(() => {
    if (!atrRef.current) return;
    const rows = overlays?.atr ?? [];
    atrRef.current.setData(rows.map((row) => ({ time: toTime(row.time), value: row.value })));
  }, [overlays]);

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
      if (visible.levels) {
        for (const row of overlays.levels.slice(-OVERLAY_CAPS.levels)) {
          addLine(row.centre, levelColor(row.kind), row.kind, LineStyle.Dashed);
        }
      }
      if (visible.msnr) {
        for (const row of overlays.msnr_levels.slice(-OVERLAY_CAPS.msnr_levels)) {
          addLine(row.price, "#ffd54f", `msnr ${row.kind}`, LineStyle.Dotted);
        }
      }
      if (visible.zones) {
        for (const row of overlays.zones.slice(-OVERLAY_CAPS.zones)) {
          const color = flowColor(row.kind);
          addLine(row.proximal, color, `${row.kind} proximal`, LineStyle.Solid);
          addLine(row.distal, color, `${row.kind} distal`, LineStyle.Dashed);
        }
      }
      if (visible.fvgs) {
        for (const row of overlays.fvgs.slice(-OVERLAY_CAPS.fvgs)) {
          const color = flowColor(row.direction);
          addLine(row.lower, color, `fvg ${row.direction}`, LineStyle.Dotted);
          addLine(row.upper, color, `fvg ${row.direction}`, LineStyle.Dotted);
        }
      }
      if (visible.orderBlocks) {
        for (const row of overlays.order_blocks.slice(-OVERLAY_CAPS.order_blocks)) {
          const color = flowColor(row.direction);
          addLine(row.lower, color, "ob", LineStyle.Dotted);
          addLine(row.upper, color, "ob", LineStyle.Dotted);
        }
      }
      if (visible.pools) {
        for (const row of overlays.pools.slice(-OVERLAY_CAPS.pools)) {
          addLine(row.level, "#b39ddb", `pool ${row.side}`, LineStyle.Dotted);
        }
      }

      if (visible.structure) {
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
      }
      if (visible.pools) {
        for (const row of overlays.grabs) {
          markers.push({
            time: toTime(row.time),
            position: row.side === "sell_side" ? "belowBar" : "aboveBar",
            shape: "circle",
            color: "#b39ddb",
            text: "grab",
          });
        }
      }
      if (visible.crt) {
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
    }

    markers.sort((a, b) => (a.time as number) - (b.time as number));
    markersApi.setMarkers(markers.slice(-OVERLAY_CAPS.markers));
  }, [overlays, visible]);

  return <div ref={containerRef} className="chart" style={{ height }} />;
}
