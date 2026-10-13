import { useEffect, useMemo, useState } from "react";
import {
  AnalysisResponse,
  CandlesResponse,
  OverlaysResponse,
  SymbolInfo,
  getAnalysis,
  getCandles,
  getOverlays,
  getSymbols,
} from "./api";
import AnalysisPanel from "./components/AnalysisPanel";
import PriceChart, { OVERLAY_CAPS } from "./components/PriceChart";

const TIMEFRAMES = ["M15", "H1", "H4", "D1"];
const FETCH_BARS = 600;
const MAX_OPTIONS = 400;

function param(key: string, fallback: string): string {
  const value = new URLSearchParams(window.location.search).get(key);
  return value && value.length > 0 ? value : fallback;
}

export default function App() {
  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
  const [symbol, setSymbol] = useState(() => param("symbol", "GBPUSD"));
  const [timeframe, setTimeframe] = useState(() => {
    const tf = param("timeframe", "H1");
    return TIMEFRAMES.includes(tf) ? tf : "H1";
  });
  const [search, setSearch] = useState("");
  const [data, setData] = useState<CandlesResponse | null>(null);
  const [overlays, setOverlays] = useState<OverlaysResponse | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [loadingChart, setLoadingChart] = useState(false);
  const [analysing, setAnalysing] = useState(false);

  useEffect(() => {
    getSymbols().then(setSymbols).catch((e) => setLoadError(String(e)));
  }, []);

  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("symbol", symbol);
    url.searchParams.set("timeframe", timeframe);
    window.history.replaceState(null, "", url.toString());
  }, [symbol, timeframe]);

  useEffect(() => {
    let cancelled = false;
    setLoadingChart(true);
    setLoadError(null);
    Promise.all([getCandles(symbol, timeframe, FETCH_BARS), getOverlays(symbol, timeframe, FETCH_BARS)])
      .then(([candles, drawn]) => {
        if (cancelled) return;
        setData(candles);
        setOverlays(drawn);
      })
      .catch((e) => {
        if (cancelled) return;
        setData(null);
        setOverlays(null);
        setLoadError(String(e));
      })
      .finally(() => !cancelled && setLoadingChart(false));
    return () => {
      cancelled = true;
    };
  }, [symbol, timeframe]);

  useEffect(() => {
    let cancelled = false;
    setAnalysing(true);
    setAnalysisError(null);
    getAnalysis(symbol, timeframe, { bars: 1200 })
      .then((body) => {
        if (!cancelled) setAnalysis(body);
      })
      .catch((e) => {
        if (!cancelled) {
          setAnalysis(null);
          setAnalysisError(String(e));
        }
      })
      .finally(() => !cancelled && setAnalysing(false));
    return () => {
      cancelled = true;
    };
  }, [symbol, timeframe]);

  const rerun = () => {
    setAnalysing(true);
    setAnalysisError(null);
    getAnalysis(symbol, timeframe, { bars: 1200, refresh: true })
      .then(setAnalysis)
      .catch((e) => {
        setAnalysis(null);
        setAnalysisError(String(e));
      })
      .finally(() => setAnalysing(false));
  };

  const options = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const rows = needle
      ? symbols.filter(
          (s) =>
            s.canonical_id.toLowerCase().includes(needle) ||
            s.display.toLowerCase().includes(needle) ||
            s.venue.toLowerCase().includes(needle)
        )
      : symbols;
    return rows.slice(0, MAX_OPTIONS);
  }, [symbols, search]);

  const current = useMemo(() => symbols.find((s) => s.canonical_id === symbol), [symbols, symbol]);

  const overlaySummary = useMemo(() => {
    if (!overlays) return null;
    const c = overlays.counts;
    return [
      ["levels", c.levels, OVERLAY_CAPS.levels],
      ["zones", c.zones, OVERLAY_CAPS.zones],
      ["pools", c.pools, OVERLAY_CAPS.pools],
      ["fvgs", c.fvgs, OVERLAY_CAPS.fvgs],
      ["order blocks", c.order_blocks, OVERLAY_CAPS.order_blocks],
      ["MSS", c.mss, OVERLAY_CAPS.markers],
    ] as Array<[string, number, number]>;
  }, [overlays]);

  const submitSearch = () => {
    const needle = search.trim().toLowerCase();
    if (!needle) return;
    const hit =
      symbols.find((s) => s.canonical_id.toLowerCase() === needle) ??
      options.find((s) => s.canonical_id.toLowerCase().includes(needle));
    if (hit) {
      setSymbol(hit.canonical_id);
      setSearch("");
    }
  };

  return (
    <div className="page">
      <header>
        <h1>Trading Analysis</h1>
        <div className="controls">
          <input
            className="search"
            placeholder="search symbol, e.g. GBPUSD"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitSearch()}
          />
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {(options.some((s) => s.canonical_id === symbol) ? options : [
              ...symbols.filter((s) => s.canonical_id === symbol),
              ...options,
            ]).map((s) => (
              <option key={s.canonical_id} value={s.canonical_id}>
                {s.display} - {s.venue}
              </option>
            ))}
          </select>
          <div className="tabs">
            {TIMEFRAMES.map((tf) => (
              <button key={tf} className={tf === timeframe ? "active" : ""} onClick={() => setTimeframe(tf)}>
                {tf}
              </button>
            ))}
          </div>
        </div>
      </header>

      <section className="meta">
        {current && (
          <span>
            {current.display} ({current.market_class}) - {current.venue}
          </span>
        )}
        {data && (
          <>
            <span>{data.count} candles</span>
            {data.stale && <span className="warn">feed looks stale</span>}
            {data.unexpected_gaps.length > 0 && (
              <span className="warn">{data.unexpected_gaps.length} unexpected gap(s)</span>
            )}
          </>
        )}
        {loadingChart && <span>loading...</span>}
      </section>

      {overlaySummary && (
        <section className="meta overlays">
          {overlaySummary.map(([label, found, cap]) => (
            <span key={label}>
              {label}: {found} found, last {Math.min(found, cap)} drawn
            </span>
          ))}
        </section>
      )}

      {loadError && <div className="error">{loadError}</div>}
      {data && data.candles.length > 0 ? (
        <PriceChart candles={data.candles} overlays={overlays} />
      ) : (
        !loadError && <div className="empty">No candles yet for this symbol. Load history first.</div>
      )}

      <AnalysisPanel analysis={analysis} loading={analysing} error={analysisError} onRefresh={rerun} />

      <footer>
        Charting by{" "}
        <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">
          TradingView Lightweight Charts
        </a>{" "}
        (Apache-2.0). Analysis only - no order execution, no advice.
      </footer>
    </div>
  );
}
