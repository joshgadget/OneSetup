import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import PriceChart, { DEFAULT_VISIBILITY, OVERLAY_CAPS, OverlayVisibility } from "./components/PriceChart";
import Watchlist from "./components/Watchlist";

const TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"];
const TF_LABEL: Record<string, string> = {
  M1: "1m",
  M5: "5m",
  M15: "15m",
  M30: "30m",
  H1: "1h",
  H4: "4h",
  D1: "1D",
  W1: "1W",
};
const FETCH_BARS = 600;
const LIVE_INTERVAL_MS = 20000;

function stored<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

function store(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode: the session simply does not remember */
  }
}

function urlParam(key: string): string | null {
  const value = new URLSearchParams(window.location.search).get(key);
  return value && value.length > 0 ? value : null;
}

export default function App() {
  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
  const [symbol, setSymbol] = useState(
    () => urlParam("symbol") ?? stored("tv.symbol", "GBPUSD")
  );
  const [timeframe, setTimeframe] = useState(() => {
    const tf = urlParam("timeframe") ?? stored("tv.timeframe", "H1");
    return TIMEFRAMES.includes(tf) ? tf : "H1";
  });
  const [candles, setCandles] = useState<CandlesResponse | null>(null);
  const [overlays, setOverlays] = useState<OverlaysResponse | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [loadingChart, setLoadingChart] = useState(false);
  const [analysing, setAnalysing] = useState(false);
  const [live, setLive] = useState(() => stored("tv.live", true));
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [showVolume, setShowVolume] = useState(() => stored("tv.volume", true));
  const [showAtr, setShowAtr] = useState(() => stored("tv.atr", true));
  const [showAll, setShowAll] = useState(() => stored("tv.showAll", false));
  const [visible, setVisible] = useState<OverlayVisibility>(() => stored("tv.visible", DEFAULT_VISIBILITY));
  const [favourites, setFavourites] = useState<string[]>(() => stored("tv.favourites", []));
  const tickRef = useRef(0);
  const chartWrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    getSymbols().then(setSymbols).catch((e) => setLoadError(String(e)));
  }, []);

  useEffect(() => store("tv.symbol", symbol), [symbol]);
  useEffect(() => store("tv.timeframe", timeframe), [timeframe]);
  useEffect(() => store("tv.live", live), [live]);
  useEffect(() => store("tv.volume", showVolume), [showVolume]);
  useEffect(() => store("tv.atr", showAtr), [showAtr]);
  useEffect(() => store("tv.visible", visible), [visible]);
  useEffect(() => store("tv.favourites", favourites), [favourites]);
  useEffect(() => store("tv.showAll", showAll), [showAll]);

  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("symbol", symbol);
    url.searchParams.set("timeframe", timeframe);
    window.history.replaceState(null, "", url.toString());
  }, [symbol, timeframe]);

  const loadChart = useCallback(
    async (refresh: boolean) => {
      setLoadingChart(true);
      setLoadError(null);
      try {
        const [nextCandles, nextOverlays] = await Promise.all([
          getCandles(symbol, timeframe, FETCH_BARS, refresh),
          getOverlays(symbol, timeframe, FETCH_BARS, refresh),
        ]);
        setCandles(nextCandles);
        setOverlays(nextOverlays);
        setLastUpdated(new Date().toLocaleTimeString());
      } catch (e) {
        setCandles(null);
        setOverlays(null);
        setLoadError(String(e));
      } finally {
        setLoadingChart(false);
      }
    },
    [symbol, timeframe]
  );

  useEffect(() => {
    void loadChart(false);
  }, [loadChart]);

  const loadAnalysis = useCallback(
    async (refresh: boolean) => {
      setAnalysing(true);
      setAnalysisError(null);
      try {
        setAnalysis(await getAnalysis(symbol, timeframe, { bars: 1200, refresh }));
      } catch (e) {
        setAnalysis(null);
        setAnalysisError(String(e));
      } finally {
        setAnalysing(false);
      }
    },
    [symbol, timeframe]
  );

  useEffect(() => {
    void loadAnalysis(false);
  }, [loadAnalysis]);

  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => {
      tickRef.current += 1;
      const refreshOverlays = tickRef.current % 3 === 0;
      void (async () => {
        try {
          const next = await getCandles(symbol, timeframe, FETCH_BARS, true);
          setCandles(next);
          setLastUpdated(new Date().toLocaleTimeString());
          if (refreshOverlays) {
            setOverlays(await getOverlays(symbol, timeframe, FETCH_BARS, true));
          }
        } catch {
          /* a failed poll keeps the last good chart on screen */
        }
      })();
    }, LIVE_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [live, symbol, timeframe]);

  const toggleFullscreen = () => {
    const element = chartWrapRef.current;
    if (!element) return;
    if (document.fullscreenElement) {
      void document.exitFullscreen();
    } else {
      void element.requestFullscreen?.();
    }
  };

  const current = useMemo(() => symbols.find((s) => s.canonical_id === symbol), [symbols, symbol]);

  const overlaySummary = useMemo(() => {
    if (!overlays) return [];
    const c = overlays.counts;
    return [
      ["levels", c.levels, OVERLAY_CAPS.levels, "levels"],
      ["zones", c.zones, OVERLAY_CAPS.zones, "zones"],
      ["pools", c.pools, OVERLAY_CAPS.pools, "pools"],
      ["fvgs", c.fvgs, OVERLAY_CAPS.fvgs, "fvgs"],
      ["order blocks", c.order_blocks, OVERLAY_CAPS.order_blocks, "orderBlocks"],
      ["MSS", c.mss, OVERLAY_CAPS.markers, "structure"],
      ["MSNR", c.msnr_levels, OVERLAY_CAPS.msnr_levels, "msnr"],
      ["CRT", c.crt, OVERLAY_CAPS.markers, "crt"],
    ] as Array<[string, number, number, keyof OverlayVisibility]>;
  }, [overlays]);

  const toggleOverlay = (key: keyof OverlayVisibility) =>
    setVisible((prev) => ({ ...prev, [key]: !prev[key] }));

  const toggleFavourite = (id: string) =>
    setFavourites((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [id, ...prev]));

  return (
    <div className="page layout">
      <Watchlist
        symbols={symbols}
        current={symbol}
        favourites={favourites}
        onSelect={setSymbol}
        onToggleFavourite={toggleFavourite}
        showAll={showAll}
        onShowAll={setShowAll}
      />

      <main className="workspace">
        <header>
          <div className="title">
            <h1>{current?.display ?? symbol}</h1>
            <span className="notes">
              {current ? `${current.market_class} - ${current.venue}` : ""}
              {lastUpdated ? ` - updated ${lastUpdated}` : ""}
              {candles?.refresh_skipped ? " - provider poll throttled, store shown" : ""}
            </span>
          </div>
          <div className="controls">
            <button className={live ? "active" : ""} onClick={() => setLive((v) => !v)}>
              {live ? "live" : "paused"}
            </button>
            <button onClick={() => void loadChart(true)} disabled={loadingChart}>
              {loadingChart ? "loading..." : "refresh"}
            </button>
            <button onClick={toggleFullscreen}>full screen</button>
          </div>
        </header>

        <div className="tabs">
          {TIMEFRAMES.map((tf) => (
            <button key={tf} className={tf === timeframe ? "active" : ""} onClick={() => setTimeframe(tf)}>
              {TF_LABEL[tf] ?? tf}
            </button>
          ))}
          <span className="spacer" />
          <button className={showVolume ? "active" : ""} onClick={() => setShowVolume((v) => !v)}>
            volume
          </button>
          <button className={showAtr ? "active" : ""} onClick={() => setShowAtr((v) => !v)}>
            ATR 14
          </button>
        </div>

        {overlaySummary.length > 0 && (
          <div className="legend">
            {overlaySummary.map(([label, found, cap, key]) => (
              <button
                key={label}
                className={visible[key] ? "chip active" : "chip"}
                onClick={() => toggleOverlay(key)}
                title={`${found} found, last ${Math.min(found, cap)} drawn`}
              >
                {label} <span className="notes">{found}</span>
              </button>
            ))}
          </div>
        )}

        {loadError && <div className="error">{loadError}</div>}
        <div ref={chartWrapRef} className="chart-wrap">
          {candles && candles.candles.length > 0 ? (
            <PriceChart
              candles={candles.candles}
              overlays={overlays}
              fitKey={`${symbol}|${timeframe}`}
              visible={visible}
              showVolume={showVolume}
              showAtr={showAtr}
            />
          ) : (
            !loadError && (
              <div className="empty">
                No candles yet for this symbol and timeframe. Turn on live refresh or load history
                for it.
              </div>
            )
          )}
        </div>
        <p className="notes">
          {candles ? `${candles.count} closed candles - source ${candles.source}` : ""}
          {candles?.stale ? " - feed looks stale" : ""}
          {candles && candles.unexpected_gaps.length > 0
            ? ` - ${candles.unexpected_gaps.length} unexpected gap(s)`
            : ""}
        </p>
      </main>

      <AnalysisPanel analysis={analysis} loading={analysing} error={analysisError} onRefresh={() => void loadAnalysis(true)} />

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
