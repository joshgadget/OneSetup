import { useEffect, useMemo, useState } from "react";
import { CandlesResponse, SymbolInfo, getCandles, getSymbols } from "./api";
import PriceChart from "./components/PriceChart";

const TIMEFRAMES = ["M15", "H1", "H4", "D1"];

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
  const [data, setData] = useState<CandlesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getSymbols().then(setSymbols).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("symbol", symbol);
    url.searchParams.set("timeframe", timeframe);
    window.history.replaceState(null, "", url.toString());
  }, [symbol, timeframe]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getCandles(symbol, timeframe)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((e) => {
        if (!cancelled) {
          setData(null);
          setError(String(e));
        }
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [symbol, timeframe]);

  const current = useMemo(() => symbols.find((s) => s.canonical_id === symbol), [symbols, symbol]);

  return (
    <div className="page">
      <header>
        <h1>Trading Analysis</h1>
        <div className="controls">
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {symbols.map((s) => (
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
        {loading && <span>loading...</span>}
      </section>

      {error && <div className="error">{error}</div>}
      {data && data.candles.length > 0 ? (
        <PriceChart candles={data.candles} />
      ) : (
        !error && <div className="empty">No candles yet for this symbol. Run scripts\seed_store.py to load history.</div>
      )}

      <footer>
        Charting by{" "}
        <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">
          TradingView Lightweight Charts
        </a>{" "}
        (Apache-2.0). Analysis only - no order execution.
      </footer>
    </div>
  );
}